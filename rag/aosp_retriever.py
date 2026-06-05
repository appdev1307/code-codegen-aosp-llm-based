"""
rag/aosp_retriever.py
"""
from __future__ import annotations

import logging
from pathlib import Path
from typing import Optional

import chromadb
from chromadb.config import Settings
from sentence_transformers import SentenceTransformer

logger = logging.getLogger(__name__)

try:
    from rank_bm25 import BM25Okapi
    _BM25_AVAILABLE = True
except ImportError:
    _BM25_AVAILABLE = False

try:
    from sentence_transformers import CrossEncoder
    _RERANKER_AVAILABLE = True
except ImportError:
    _RERANKER_AVAILABLE = False

COLLECTION_MAP: dict[str, str | list[str]] = {
    "aidl":           "aosp_aidl",
    "cpp":            "aosp_cpp",
    "selinux":        "aosp_selinux",
    "build":          "aosp_build",
    "vintf":          "aosp_vintf",
    "android_app":    "aosp_car_api",
    "android_layout": "aosp_car_api",
    "design_doc":     ["aosp_docs", "aosp_cpp"],
    "puml":           ["aosp_docs", "aosp_cpp"],
    "backend":        "aosp_cpp",
    "backend_model":  "aosp_cpp",
    "simulator":      "aosp_cpp",
}

DEFAULT_TOP_K       = 6      # was 3 — cpp method signatures need more chunks
MIN_SCORE_THRESHOLD = 0.25
EMBEDDING_MODEL     = "all-MiniLM-L6-v2"   # keep in sync with indexer
RERANKER_MODEL      = "BAAI/bge-reranker-base"


class AOSPRetriever:
    def __init__(
        self,
        db_path: str | Path = "rag/chroma_db",
        embedding_model: str = EMBEDDING_MODEL,
        default_top_k: int = DEFAULT_TOP_K,
        min_score: float = MIN_SCORE_THRESHOLD,
        use_reranker: bool = True,
    ):
        self.db_path       = Path(db_path)
        self.default_top_k = default_top_k
        self.min_score     = min_score

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

        # BM25 cache keyed by collection name — built lazily
        self._bm25_index:  dict[str, object]     = {}
        self._bm25_corpus: dict[str, list[dict]] = {}

        # Optional cross-encoder reranker
        self._reranker = None
        if use_reranker and _RERANKER_AVAILABLE:
            try:
                self._reranker = CrossEncoder(RERANKER_MODEL)
            except Exception as e:
                logger.warning(f"[RAG] Reranker load failed: {e}")

        logger.info(f"[RAG Retriever] Ready — DB: {self.db_path}")

    # ── public API (signatures unchanged) ────────────────────────────────────

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

        top_k     = top_k or self.default_top_k
        cache_key = f"{agent_type}::{top_k}::{query}"
        if cache_key in self._query_cache:
            return self._query_cache[cache_key]

        collection_names = COLLECTION_MAP[agent_type]
        if isinstance(collection_names, str):
            collection_names = [collection_names]

        embedding   = self._embed(query)
        all_results: list[dict] = []
        seen_ids:    set[str]   = set()
        per_col_k   = max(2, top_k // len(collection_names) + 1)

        for col_name in collection_names:
            col = self._get_collection(col_name)
            if not col or col.count() == 0:
                continue
            try:
                raw = col.query(
                    query_embeddings=[embedding],
                    n_results=min(per_col_k * 2, col.count()),
                    where={"filename": {"$contains": filter_filename}} if filter_filename else None,
                    include=["documents", "metadatas", "distances"],
                )
                for r in self._parse_results(raw, per_col_k):
                    uid = f"{r['file']}::{r['chunk_index']}"
                    if uid not in seen_ids:
                        seen_ids.add(uid)
                        all_results.append(r)
            except Exception as e:
                logger.error(f"Query failed for {col_name}: {e}")

        all_results.sort(key=lambda x: x["score"], reverse=True)
        result = all_results[:top_k]
        self._query_cache[cache_key] = result
        return result

    def retrieve_multi(
        self,
        queries: list[str],
        agent_type: str,
        top_k: Optional[int] = None,
    ) -> list[dict]:
        """Hybrid: dense × N queries + BM25 (if available) → optional rerank."""
        top_k_eff = top_k or self.default_top_k
        seen_ids: set[str]  = set()
        merged:   list[dict] = []

        # dense channel
        for q in queries:
            for r in self.retrieve(q, agent_type, top_k=top_k_eff):
                uid = f"{r['file']}::{r['chunk_index']}"
                if uid not in seen_ids:
                    seen_ids.add(uid)
                    merged.append(r)

        # BM25 sparse channel
        if _BM25_AVAILABLE:
            col_names = COLLECTION_MAP.get(agent_type, [])
            if isinstance(col_names, str):
                col_names = [col_names]
            for col_name in col_names:
                for q in queries:
                    for r in self._bm25_retrieve(q, col_name, top_k_eff):
                        uid = f"{r['file']}::{r['chunk_index']}"
                        if uid not in seen_ids:
                            seen_ids.add(uid)
                            merged.append(r)

        # rerank
        if self._reranker and merged:
            combined_q = " ".join(queries)
            pairs  = [[combined_q, r["text"]] for r in merged]
            scores = self._reranker.predict(pairs)
            for r, s in zip(merged, scores):
                r["score"] = float(s)

        merged.sort(key=lambda x: x["score"], reverse=True)
        return merged[:top_k_eff]

    def format_for_prompt(
        self,
        retrieved: list[dict],
        label: str = "AOSP Reference",
        max_chars_per_chunk: int = 1500,   # was 800 — sigs were truncated
    ) -> str:
        if not retrieved:
            return ""
        lines = [
            f"### {label}",
            f"# {len(retrieved)} example(s) from real AOSP source.",
            "# Match signatures exactly.",
            "",
        ]
        for i, r in enumerate(retrieved, 1):
            text = r["text"]
            if len(text) > max_chars_per_chunk:
                text = text[:max_chars_per_chunk] + "\n// ... [truncated]"
            lines += [
                f"// --- Example {i} | {r['filename']} | score: {r['score']:.2f} ---",
                text, "",
            ]
        return "\n".join(lines)

    # ── private ──────────────────────────────────────────────────────────────

    @property
    def embedder(self) -> SentenceTransformer:
        if self._embedder is None:
            self._embedder = SentenceTransformer(self._embedding_model_name)
        return self._embedder

    def _embed(self, text: str) -> list[float]:
        return self.embedder.encode(
            [text], normalize_embeddings=True, show_progress_bar=False
        )[0].tolist()

    def _get_collection(self, name: str) -> Optional[chromadb.Collection]:
        if name in self._collections:
            return self._collections[name]
        try:
            col = self.client.get_collection(name)
            self._collections[name] = col
            return col
        except Exception:
            logger.warning(f"Collection '{name}' not found.")
            return None

    def _parse_results(self, raw: dict, top_k: int) -> list[dict]:
        HIDL_PATH = ("/2.0/", "/1.0/", "/3.0/", "/hidl/",
                     "/vehicle/2.0/", "/vehicle/1.0/", "/v2_0/", "/v1_0/")
        results = []
        for doc, meta, dist in zip(
            raw.get("documents", [[]])[0],
            raw.get("metadatas",  [[]])[0],
            raw.get("distances",  [[]])[0],
        ):
            score = round(1.0 - dist, 4)
            if score < self.min_score:
                continue
            if any(m in meta.get("file", "").lower() for m in HIDL_PATH):
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

    def _ensure_bm25(self, col_name: str) -> bool:
        if col_name in self._bm25_index:
            return True
        if not _BM25_AVAILABLE:
            return False
        col = self._get_collection(col_name)
        if not col or col.count() == 0:
            return False
        try:
            all_docs = col.get(include=["documents", "metadatas"])
            HIDL = ("/2.0/", "/1.0/", "/hidl/", "/v2_0/")
            corpus = [
                {"text": d, "file": m.get("file",""), "filename": m.get("filename",""),
                 "chunk_index": m.get("chunk_index", 0)}
                for d, m in zip(all_docs.get("documents",[]), all_docs.get("metadatas",[]))
                if not any(h in m.get("file","").lower() for h in HIDL)
            ]
            if not corpus:
                return False
            self._bm25_corpus[col_name] = corpus
            self._bm25_index[col_name]  = BM25Okapi([c["text"].split() for c in corpus])
            return True
        except Exception as e:
            logger.warning(f"[BM25] build failed for {col_name}: {e}")
            return False

    def _bm25_retrieve(self, query: str, col_name: str, top_k: int) -> list[dict]:
        if not self._ensure_bm25(col_name):
            return []
        try:
            scores = self._bm25_index[col_name].get_scores(query.split())
            corpus = self._bm25_corpus[col_name]
            top    = sorted(range(len(scores)), key=lambda i: scores[i], reverse=True)[:top_k]
            return [{**corpus[i], "score": round(float(scores[i]), 4)}
                    for i in top if scores[i] > 0]
        except Exception:
            return []

    def is_ready(self) -> bool:
        try:
            return len(self.client.list_collections()) > 0
        except Exception:
            return False

    def collection_stats(self) -> dict[str, int]:
        stats, seen = {}, set()
        for col_names in COLLECTION_MAP.values():
            if isinstance(col_names, str):
                col_names = [col_names]
            for n in col_names:
                if n not in seen:
                    seen.add(n)
                    col = self._get_collection(n)
                    stats[n] = col.count() if col else 0
        return stats


_SHARED_CLIENT:    Optional[chromadb.ClientAPI] = None
_shared_retriever: Optional[AOSPRetriever]      = None


def get_retriever(db_path: str = "rag/chroma_db", **kwargs) -> AOSPRetriever:
    global _shared_retriever
    if _shared_retriever is None:
        _shared_retriever = AOSPRetriever(db_path=db_path, **kwargs)
        global _SHARED_CLIENT
        if _SHARED_CLIENT is None:
            _SHARED_CLIENT = _shared_retriever.client
    return _shared_retriever


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO, format="%(levelname)s: %(message)s")
    r = get_retriever()
    print("Collection stats:")
    for col, count in r.collection_stats().items():
        print(f"  {col:<25} {count:>6} chunks")