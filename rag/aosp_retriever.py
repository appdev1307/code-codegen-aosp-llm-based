"""
rag/aosp_retriever.py
────────────────────────────────────────────────────────────────────
Retrieves relevant AOSP HAL source examples from ChromaDB.
With strong post-filtering to remove legacy HIDL content.
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

# Agent-type → Collection mapping
COLLECTION_MAP: dict[str, str | list[str]] = {
    "aidl":          "aosp_aidl",
    "cpp":           "aosp_cpp",
    "selinux":       "aosp_selinux",
    "build":         "aosp_build",
    "vintf":         "aosp_vintf",
    "android_app":   "aosp_car_api",
    "android_layout":"aosp_car_api",
    "design_doc":    ["aosp_docs", "aosp_cpp"],
    "puml":          ["aosp_docs", "aosp_cpp"],
    "backend":       "aosp_cpp",
    "backend_model": "aosp_cpp",
    "simulator":     "aosp_cpp",
}

DEFAULT_TOP_K = 3
MIN_SCORE_THRESHOLD = 0.25
EMBEDDING_MODEL = "all-MiniLM-L6-v2"


class AOSPRetriever:
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
            raise FileNotFoundError(f"ChromaDB not found at: {self.db_path}")

        global _SHARED_CLIENT
        if _SHARED_CLIENT is not None:
            self.client = _SHARED_CLIENT
        else:
            self.client = chromadb.PersistentClient(
                path=str(self.db_path),
                settings=Settings(anonymized_telemetry=False),
            )
            _SHARED_CLIENT = self.client

        self._embedder: Optional[SentenceTransformer] = None
        self._embedding_model_name = embedding_model
        self._collections: dict[str, chromadb.Collection] = {}
        self._query_cache: dict[str, list[dict]] = {}

        logger.info(f"[RAG Retriever] Ready — DB: {self.db_path}")

    # ====================== PUBLIC API ======================

    def retrieve(
        self,
        query: str,
        agent_type: str,
        top_k: Optional[int] = None,
        filter_filename: Optional[str] = None,
    ) -> list[dict]:
        if agent_type not in COLLECTION_MAP:
            logger.warning(f"Unknown agent_type '{agent_type}'")
            return []

        top_k = top_k or self.default_top_k
        cache_key = f"{agent_type}::{top_k}::{query}"

        if cache_key in self._query_cache:
            return self._query_cache[cache_key]

        collection_names = COLLECTION_MAP[agent_type]
        if isinstance(collection_names, str):
            collection_names = [collection_names]

        embedding = self._embed(query)

        all_retrieved: list[dict] = []
        seen_ids: set[str] = set()
        per_col_k = max(2, top_k // len(collection_names) + 1)

        for collection_name in collection_names:
            collection = self._get_collection(collection_name)
            if not collection or collection.count() == 0:
                continue

            try:
                results = collection.query(
                    query_embeddings=[embedding],
                    n_results=min(per_col_k * 2, collection.count()),
                    where={"filename": {"$contains": filter_filename}} if filter_filename else None,
                    include=["documents", "metadatas", "distances"],
                )
                for r in self._parse_results(results, per_col_k):
                    uid = f"{r['file']}::{r['chunk_index']}"
                    if uid not in seen_ids:
                        seen_ids.add(uid)
                        all_retrieved.append(r)
            except Exception as e:
                logger.error(f"Query failed for {collection_name}: {e}")

        all_retrieved.sort(key=lambda x: x["score"], reverse=True)
        retrieved = all_retrieved[:top_k]

        self._query_cache[cache_key] = retrieved
        return retrieved

    def retrieve_multi(self, queries: list[str], agent_type: str, top_k: Optional[int] = None) -> list[dict]:
        seen_ids: set[str] = set()
        merged: list[dict] = []

        for q in queries:
            for result in self.retrieve(q, agent_type, top_k=top_k or self.default_top_k):
                uid = f"{result['file']}::{result['chunk_index']}"
                if uid not in seen_ids:
                    seen_ids.add(uid)
                    merged.append(result)

        merged.sort(key=lambda x: x["score"], reverse=True)
        return merged[: (top_k or self.default_top_k)]

    def format_for_prompt(
        self,
        retrieved: list[dict],
        label: str = "AOSP Reference",
        max_chars_per_chunk: int = 800,
    ) -> str:
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

    # ====================== PRIVATE HELPERS ======================

    @property
    def embedder(self) -> SentenceTransformer:
        if self._embedder is None:
            logger.info(f"[RAG Retriever] Loading embedding model: {self._embedding_model_name}")
            self._embedder = SentenceTransformer(self._embedding_model_name)
        return self._embedder

    def _embed(self, text: str) -> list[float]:
        vec = self.embedder.encode([text], normalize_embeddings=True, show_progress_bar=False)
        return vec[0].tolist()

    def _get_collection(self, collection_name: str) -> Optional[chromadb.Collection]:
        if collection_name in self._collections:
            return self._collections[collection_name]
        try:
            col = self.client.get_collection(collection_name)
            self._collections[collection_name] = col
            return col
        except Exception:
            logger.warning(f"Collection '{collection_name}' not found.")
            return None

    def _parse_results(self, raw: dict, top_k: int) -> list[dict]:
        """Parse ChromaDB results with strong HIDL post-filtering."""
        results = []

        docs      = raw.get("documents", [[]])[0]
        metas     = raw.get("metadatas",  [[]])[0]
        distances = raw.get("distances",  [[]])[0]

        # Strong HIDL filter keywords
        HIDL_FILTER = {
            "hidl::", "@2.0", "@1.0", "V2_0", "V1_0", "BpHw", "BnHw",
            "android.hardware.automotive.vehicle@2.0", "IVehicle.hidl",
            "FakeObd2", "VehiclePropertyStore", "VehicleObjectPool",
            "Obd2SensorStore", "AccessForVehicleProperty"
        }

        for doc, meta, dist in zip(docs, metas, distances):
            score = round(1.0 - dist, 4)
            if score < self.min_score:
                continue

            text_lower = doc.lower()
            filename_lower = meta.get("filename", "").lower()

            # Skip if clearly legacy HIDL content
            if any(kw.lower() in text_lower or kw.lower() in filename_lower for kw in HIDL_FILTER):
                continue

            results.append({
                "text":        doc,
                "file":        meta.get("file", ""),
                "filename":    meta.get("filename", ""),
                "suffix":      meta.get("suffix", ""),
                "chunk_index": meta.get("chunk_index", 0),
                "score":       score,
            })

        results.sort(key=lambda x: x["score"], reverse=True)
        return results[:top_k]

    def is_ready(self) -> bool:
        try:
            return len(self.client.list_collections()) > 0
        except Exception:
            return False

    def collection_stats(self) -> dict[str, int]:
        stats = {}
        seen = set()
        for agent_type, col_names in COLLECTION_MAP.items():
            if isinstance(col_names, str):
                col_names = [col_names]
            for col_name in col_names:
                if col_name in seen:
                    continue
                seen.add(col_name)
                col = self._get_collection(col_name)
                stats[col_name] = col.count() if col else 0
        return stats


# Module-level singletons
_SHARED_CLIENT: Optional[chromadb.ClientAPI] = None
_shared_retriever: Optional[AOSPRetriever] = None


def get_retriever(db_path: str = "rag/chroma_db", **kwargs) -> AOSPRetriever:
    global _shared_retriever
    if _shared_retriever is None:
        _shared_retriever = AOSPRetriever(db_path=db_path, **kwargs)
        global _SHARED_CLIENT
        if _SHARED_CLIENT is None:
            _SHARED_CLIENT = _shared_retriever.client
    return _shared_retriever


# Smoke test
if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO, format="%(levelname)s: %(message)s")
    retriever = get_retriever()
    print("Collection stats:")
    for col, count in retriever.collection_stats().items():
        print(f"  {col:<25} {count:>6} chunks")