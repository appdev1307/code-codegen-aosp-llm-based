"""
rag/aosp_retriever.py
────────────────────────────────────────────────────────────────────
Retrieves relevant AOSP HAL source examples from ChromaDB at
generation time. Called by every RAG+DSPy agent before building
its LLM prompt.

Each agent_type maps to a specific ChromaDB collection so retrieval
stays domain-relevant:
  - "aidl"         → aosp_aidl       (.aidl files)
  - "cpp"          → aosp_cpp        (.cpp/.h files)
  - "selinux"      → aosp_selinux    (.te policy files)
  - "build"        → aosp_build      (Android.bp files)
  - "vintf"        → aosp_vintf      (manifest.xml, init.rc)
  - "android_app"  → aosp_car_api    (Kotlin/Java Car API)
  - "design_doc"   → aosp_docs       (Markdown/RST docs)
  - "backend"      → aosp_docs       (reuses docs; no dedicated Python AOSP source)

Usage:
    retriever = AOSPRetriever()
    results   = retriever.retrieve(
                    query="ABS IsEnabled boolean READ_WRITE ADAS",
                    agent_type="aidl",
                    top_k=3
                )
    context   = retriever.format_for_prompt(results)
────────────────────────────────────────────────────────────────────
"""

from __future__ import annotations

import logging
import time
from functools import lru_cache
from pathlib import Path
from typing import Optional

import chromadb
from chromadb.config import Settings
from sentence_transformers import SentenceTransformer

logger = logging.getLogger(__name__)

# ─────────────────────────────────────────────────────────────────
# Agent-type → ChromaDB collection mapping
# Must match collection names created by AOSPIndexer
# ─────────────────────────────────────────────────────────────────
COLLECTION_MAP: dict[str, str] = {
    "aidl":          "aosp_aidl",
    "cpp":           "aosp_cpp",
    "selinux":       "aosp_selinux",
    "build":         "aosp_build",
    "vintf":         "aosp_vintf",
    "android_app":   "aosp_car_api",
    "android_layout":"aosp_car_api",   # layouts use same Car API source
    "design_doc":    "aosp_docs",
    "puml":          "aosp_docs",      # PlantUML borrows from docs context
    "backend":       "aosp_docs",      # no dedicated Python AOSP source
    "backend_model": "aosp_docs",
    "simulator":     "aosp_docs",
}

# Default number of chunks to retrieve
DEFAULT_TOP_K = 3

# Minimum relevance score to include a result (cosine similarity, 0-1)
MIN_SCORE_THRESHOLD = 0.25

# Embedding model — must match what AOSPIndexer used
EMBEDDING_MODEL = "all-MiniLM-L6-v2"


class AOSPRetriever:
    """
    Retrieves AOSP HAL source examples from a ChromaDB vector store.

    Parameters
    ----------
    db_path : str | Path
        Path to the ChromaDB database built by AOSPIndexer
    embedding_model : str
        SentenceTransformer model — must match what was used during indexing
    default_top_k : int
        Default number of results to return per query
    min_score : float
        Minimum cosine similarity score to include a result
    """

    def __init__(
        self,
        db_path: str | Path = "rag/chroma_db",
        embedding_model: str = EMBEDDING_MODEL,
        default_top_k: int = DEFAULT_TOP_K,
        min_score: float = MIN_SCORE_THRESHOLD,
    ):
        self.db_path = Path(db_path)
        self.default_top_k = default_top_k
        self.min_score = min_score

        if not self.db_path.exists():
            raise FileNotFoundError(
                f"ChromaDB not found at: {self.db_path}\n"
                f"Run AOSPIndexer first: python -m rag.aosp_indexer"
            )

        self.client = chromadb.PersistentClient(
            path=str(self.db_path),
            settings=Settings(anonymized_telemetry=False),
        )

        # Lazy-load embedding model (shared across all agents via singleton)
        self._embedder: Optional[SentenceTransformer] = None
        self._embedding_model_name = embedding_model

        # Cache open collection handles to avoid repeated lookups
        self._collections: dict[str, chromadb.Collection] = {}

        # Simple in-memory query cache to avoid duplicate embedding calls
        self._query_cache: dict[str, list[dict]] = {}

        logger.info(f"[RAG Retriever] Ready — DB: {self.db_path}")

    # ──────────────────────────────────────────────
    # Public API
    # ──────────────────────────────────────────────

    def retrieve(
        self,
        query: str,
        agent_type: str,
        top_k: Optional[int] = None,
        filter_filename: Optional[str] = None,
    ) -> list[dict]:
        """
        Retrieve the most relevant AOSP source chunks for a query.

        Parameters
        ----------
        query : str
            Natural language or code description of what to generate.
            Example: "ABS IsEnabled boolean READ_WRITE ADAS HAL AIDL interface"
        agent_type : str
            One of the keys in COLLECTION_MAP — determines which collection
            to search.
        top_k : int, optional
            Number of results to return. Defaults to self.default_top_k.
        filter_filename : str, optional
            If set, only return results from files whose name contains this string.
            Example: filter_filename="DefaultVehicleHal"

        Returns
        -------
        list[dict]
            Ranked list of retrieved chunks:
            [
              {
                "text":     str,    # the source chunk text
                "file":     str,    # full path of source file
                "filename": str,    # basename of source file
                "suffix":   str,    # file extension
                "score":    float,  # cosine similarity (0-1, higher=better)
                "chunk_index": int  # position within the source file
              },
              ...
            ]
        """
        if agent_type not in COLLECTION_MAP:
            logger.warning(
                f"[RAG Retriever] Unknown agent_type '{agent_type}'. "
                f"Valid types: {list(COLLECTION_MAP.keys())}"
            )
            return []

        top_k = top_k or self.default_top_k
        cache_key = f"{agent_type}::{top_k}::{query}"

        if cache_key in self._query_cache:
            logger.debug(f"[RAG Retriever] Cache hit for query: {query[:60]}")
            return self._query_cache[cache_key]

        collection_name = COLLECTION_MAP[agent_type]
        collection = self._get_collection(collection_name)
        if collection is None:
            return []

        # Check collection has documents
        if collection.count() == 0:
            logger.warning(
                f"[RAG Retriever] Collection '{collection_name}' is empty. "
                f"Run AOSPIndexer with --force to rebuild."
            )
            return []

        # Embed query
        t0 = time.time()
        embedding = self._embed(query)
        embed_time = time.time() - t0

        # Build optional metadata filter
        where = None
        if filter_filename:
            where = {"filename": {"$contains": filter_filename}}

        # Query ChromaDB
        try:
            results = collection.query(
                query_embeddings=[embedding],
                n_results=min(top_k * 2, collection.count()),  # over-fetch then filter
                where=where,
                include=["documents", "metadatas", "distances"],
            )
        except Exception as e:
            logger.error(f"[RAG Retriever] Query failed for '{agent_type}': {e}")
            return []

        # Parse and filter results
        retrieved = self._parse_results(results, top_k)

        logger.debug(
            f"[RAG Retriever] {agent_type}: {len(retrieved)} results "
            f"(embed {embed_time:.2f}s, collection={collection_name})"
        )

        self._query_cache[cache_key] = retrieved
        return retrieved

    def retrieve_multi(
        self,
        queries: list[str],
        agent_type: str,
        top_k: Optional[int] = None,
    ) -> list[dict]:
        """
        Retrieve results for multiple queries and deduplicate.
        Useful when a module has many signals — query one per signal type,
        then merge the results.

        Parameters
        ----------
        queries : list[str]
            Multiple query strings (one per signal or sub-component)
        agent_type : str
            Collection to search
        top_k : int, optional
            Results per query before dedup

        Returns
        -------
        list[dict]
            Deduplicated, re-ranked results
        """
        seen_ids: set[str] = set()
        merged: list[dict] = []

        for q in queries:
            for result in self.retrieve(q, agent_type, top_k=top_k or self.default_top_k):
                uid = f"{result['file']}::{result['chunk_index']}"
                if uid not in seen_ids:
                    seen_ids.add(uid)
                    merged.append(result)

        # Re-rank by score descending after merge
        merged.sort(key=lambda x: x["score"], reverse=True)

        # Return top_k after dedup
        final_k = top_k or self.default_top_k
        return merged[:final_k]

    def format_for_prompt(
        self,
        retrieved: list[dict],
        label: str = "AOSP Reference",
        max_chars_per_chunk: int = 800,
    ) -> str:
        """
        Format retrieved chunks into a string ready for LLM prompt injection.

        Parameters
        ----------
        retrieved : list[dict]
            Output of retrieve() or retrieve_multi()
        label : str
            Section header shown to the LLM
        max_chars_per_chunk : int
            Truncate individual chunks to this length to control prompt size

        Returns
        -------
        str
            Formatted context block, or "" if retrieved is empty
        """
        if not retrieved:
            return ""

        lines = [
            f"### {label}",
            f"# The following {len(retrieved)} example(s) are from real AOSP source.",
            "# Use them as structural reference — do not copy verbatim.",
            "",
        ]

        for i, r in enumerate(retrieved, 1):
            chunk_text = r["text"]
            if len(chunk_text) > max_chars_per_chunk:
                chunk_text = chunk_text[:max_chars_per_chunk] + "\n// ... [truncated]"

            lines += [
                f"// --- Example {i} | {r['filename']} | relevance: {r['score']:.2f} ---",
                chunk_text,
                "",
            ]

        return "\n".join(lines)

    def is_ready(self) -> bool:
        """Return True if the ChromaDB index exists and has at least one collection."""
        try:
            cols = self.client.list_collections()
            return len(cols) > 0
        except Exception:
            return False

    def collection_stats(self) -> dict[str, int]:
        """Return chunk counts per collection — useful for health checks."""
        stats = {}
        for agent_type, col_name in COLLECTION_MAP.items():
            col = self._get_collection(col_name)
            stats[col_name] = col.count() if col else 0
        return stats

    # ──────────────────────────────────────────────
    # Private helpers
    # ──────────────────────────────────────────────

    @property
    def embedder(self) -> SentenceTransformer:
        """Lazy-load the embedding model (expensive, only instantiate once)."""
        if self._embedder is None:
            logger.info(f"[RAG Retriever] Loading embedding model: {self._embedding_model_name}")
            self._embedder = SentenceTransformer(self._embedding_model_name)
        return self._embedder

    def _embed(self, text: str) -> list[float]:
        """Embed a single query string."""
        vec = self.embedder.encode(
            [text],
            normalize_embeddings=True,
            show_progress_bar=False,
        )
        return vec[0].tolist()

    def _get_collection(self, collection_name: str) -> Optional[chromadb.Collection]:
        """Get a cached ChromaDB collection handle."""
        if collection_name in self._collections:
            return self._collections[collection_name]
        try:
            col = self.client.get_collection(collection_name)
            self._collections[collection_name] = col
            return col
        except Exception:
            logger.warning(
                f"[RAG Retriever] Collection '{collection_name}' not found. "
                f"Run AOSPIndexer to build it."
            )
            return None

    def _parse_results(self, raw: dict, top_k: int) -> list[dict]:
        """
        Parse ChromaDB query output into clean result dicts.
        Filters by min_score and returns at most top_k results.
        """
        results = []

        docs      = raw.get("documents", [[]])[0]
        metas     = raw.get("metadatas",  [[]])[0]
        distances = raw.get("distances",  [[]])[0]

        for doc, meta, dist in zip(docs, metas, distances):
            # ChromaDB cosine distance is 1 - similarity when space="cosine"
            score = round(1.0 - dist, 4)

            if score < self.min_score:
                continue

            results.append({
                "text":        doc,
                "file":        meta.get("file", ""),
                "filename":    meta.get("filename", ""),
                "suffix":      meta.get("suffix", ""),
                "chunk_index": meta.get("chunk_index", 0),
                "score":       score,
            })

        # Sort by score descending, return top_k
        results.sort(key=lambda x: x["score"], reverse=True)
        return results[:top_k]


# ─────────────────────────────────────────────────────────────────
# Module-level singleton — shared across all agents in a process
# avoids reloading the embedding model for every agent call
# ─────────────────────────────────────────────────────────────────
_shared_retriever: Optional[AOSPRetriever] = None


def get_retriever(
    db_path: str = "rag/chroma_db",
    **kwargs,
) -> AOSPRetriever:
    """
    Return a process-level shared AOSPRetriever instance.
    Avoids loading the embedding model multiple times when
    many agents run in parallel threads.

    Usage in agents:
        from rag.aosp_retriever import get_retriever
        retriever = get_retriever()
    """
    global _shared_retriever
    if _shared_retriever is None:
        _shared_retriever = AOSPRetriever(db_path=db_path, **kwargs)
    return _shared_retriever


# ─────────────────────────────────────────────────────────────────
# Quick smoke-test
# python -m rag.aosp_retriever
# ─────────────────────────────────────────────────────────────────
if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO, format="%(levelname)s: %(message)s")

    print("[RAG Retriever] Running smoke test...\n")

    retriever = AOSPRetriever()

    if not retriever.is_ready():
        print("ERROR: ChromaDB index not found. Run AOSPIndexer first.")
        exit(1)

    print("Collection stats:")
    for col, count in retriever.collection_stats().items():
        print(f"  {col:<25} {count:>6} chunks")
    print()

    # Test one query per agent type
    test_queries = [
        ("aidl",        "ABS IsEnabled boolean READ_WRITE ADAS VHAL interface"),
        ("cpp",         "VHAL C++ service implementation DefaultVehicleHal"),
        ("selinux",     "hal_vehicle SELinux policy allow binder"),
        ("build",       "aidl_interface Android.bp vehicle HAL"),
        ("vintf",       "VINTF manifest HAL vehicle 2.0"),
        ("android_app", "CarPropertyManager getProperty VEHICLE_SPEED Kotlin"),
        ("design_doc",  "ADAS HAL architecture design document"),
        ("backend",     "FastAPI REST endpoint vehicle property"),
    ]

    for agent_type, query in test_queries:
        print(f"[{agent_type}] Query: {query[:60]}")
        results = retriever.retrieve(query, agent_type=agent_type, top_k=2)
        if results:
            for r in results:
                print(f"  score={r['score']:.3f}  file={r['filename']}")
        else:
            print("  (no results — check index)")
        print()