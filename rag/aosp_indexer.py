"""
rag/aosp_indexer.py
────────────────────────────────────────────────────────────────────
Crawls AOSP HAL source directories and builds a ChromaDB vector index.

Run this ONCE before any pipeline experiments:
    python -m rag.aosp_indexer --source aosp_source --db rag/chroma_db

AOSP source repos to clone first (total ~2-3 GB):
    git clone https://android.googlesource.com/platform/hardware/interfaces   aosp_source/hardware
    git clone https://android.googlesource.com/platform/system/sepolicy       aosp_source/sepolicy
    git clone https://android.googlesource.com/platform/packages/services/Car aosp_source/car

Each repo feeds a separate ChromaDB collection so retrieval stays
agent-specific (e.g. AIDL agent only searches .aidl files).
────────────────────────────────────────────────────────────────────
"""

from __future__ import annotations

import argparse
import hashlib
import json
import logging
import re
import time
from pathlib import Path
from typing import Iterator

import chromadb
from chromadb.config import Settings
from sentence_transformers import SentenceTransformer

logger = logging.getLogger(__name__)

# ─────────────────────────────────────────────────────────────────
# Collection definitions
#
# Each entry maps:
#   collection_name → {
#       extensions:  file suffixes to include
#       source_dirs: subdirectories inside aosp_source_dir to walk
#       description: human-readable label for logging
#   }
# ─────────────────────────────────────────────────────────────────
COLLECTION_DEFS: dict[str, dict] = {
    "aosp_aidl": {
        "extensions":  {".aidl"},
        "source_dirs": ["hardware"],
        "description": "AIDL interface definitions",
    },
    "aosp_cpp": {
        "extensions":  {".cpp", ".h", ".cc"},
        "source_dirs": ["hardware"],
        "description": "VHAL C++ implementation files",
    },
    "aosp_build": {
        "extensions":  {".bp"},
        "source_dirs": ["hardware", "car"],
        "description": "Android.bp build files",
    },
    "aosp_selinux": {
        "extensions":  {".te"},
        "source_dirs": ["sepolicy"],
        "description": "SELinux policy files",
        # file_contexts, property_contexts, service_contexts have no suffix —
        # matched by name pattern below
        "name_patterns": [
            r"file_contexts$",
            r"property_contexts$",
            r"service_contexts$",
            r"hwservice_contexts$",
        ],
    },
    "aosp_vintf": {
        "extensions":  {".xml", ".rc"},
        "source_dirs": ["hardware"],
        "description": "VINTF manifest and init.rc files",
        # Restrict to relevant filenames to avoid pulling in unrelated XML
        "name_patterns": [
            r"manifest.*\.xml$",
            r"compatibility_matrix.*\.xml$",
            r".*\.rc$",
        ],
    },
    "aosp_car_api": {
        "extensions":  {".kt", ".java"},
        "source_dirs": ["car"],
        "description": "Car API Kotlin/Java source files",
    },
    "aosp_docs": {
        "extensions":  {".md", ".rst", ".txt"},
        "source_dirs": ["hardware", "car"],
        "description": "AOSP design documents and READMEs",
    },
}

# Embedding model — small and fast, good enough for code retrieval
EMBEDDING_MODEL = "all-MiniLM-L6-v2"

# Token chunk size (approximate words per chunk)
CHUNK_SIZE_WORDS = 400
CHUNK_OVERLAP_WORDS = 50

# ChromaDB batch size
BATCH_SIZE = 256

# Minimum file size to index (bytes) — skip stubs and empty files
MIN_FILE_BYTES = 64

# Maximum file size to index (bytes) — skip huge generated files
MAX_FILE_BYTES = 200_000


class AOSPIndexer:
    """
    Indexes AOSP HAL source files into a ChromaDB persistent vector store.

    Parameters
    ----------
    aosp_source_dir : str | Path
        Root directory containing cloned AOSP repos
        (expects subdirs: hardware/, sepolicy/, car/)
    db_path : str | Path
        Where to store the ChromaDB database
    embedding_model : str
        SentenceTransformer model name
    force_reindex : bool
        If True, drop and rebuild existing collections
    """

    def __init__(
        self,
        aosp_source_dir: str | Path = "aosp_source",
        db_path: str | Path = "rag/chroma_db",
        embedding_model: str = EMBEDDING_MODEL,
        force_reindex: bool = False,
    ):
        self.source_dir = Path(aosp_source_dir)
        self.db_path = Path(db_path)
        self.force_reindex = force_reindex

        self.db_path.mkdir(parents=True, exist_ok=True)

        self.client = chromadb.PersistentClient(
            path=str(self.db_path),
            settings=Settings(anonymized_telemetry=False),
        )

        logger.info(f"[RAG Indexer] Loading embedding model: {embedding_model}")
        self.embedder = SentenceTransformer(embedding_model)

        # Stats collected during indexing
        self._stats: dict[str, dict] = {}

    # ──────────────────────────────────────────────
    # Public API
    # ──────────────────────────────────────────────

    def index(self) -> dict[str, dict]:
        """
        Main entry point. Indexes all collections defined in COLLECTION_DEFS.

        Returns
        -------
        dict
            Per-collection statistics: {collection_name: {files, chunks, skipped}}
        """
        if not self.source_dir.exists():
            raise FileNotFoundError(
                f"AOSP source dir not found: {self.source_dir}\n"
                f"Clone the required repos first — see module docstring."
            )

        t_start = time.time()
        print(f"\n[RAG Indexer] Starting AOSP source indexing")
        print(f"  Source: {self.source_dir.resolve()}")
        print(f"  DB:     {self.db_path.resolve()}")
        print(f"  Model:  {EMBEDDING_MODEL}")
        print()

        for collection_name, cfg in COLLECTION_DEFS.items():
            self._index_collection(collection_name, cfg)

        elapsed = time.time() - t_start
        self._print_summary(elapsed)
        self._save_index_manifest()
        return self._stats

    def collection_exists(self, collection_name: str) -> bool:
        """Check if a collection has already been indexed."""
        try:
            col = self.client.get_collection(collection_name)
            return col.count() > 0
        except Exception:
            return False

    # ──────────────────────────────────────────────
    # Private helpers
    # ──────────────────────────────────────────────

    def _index_collection(self, name: str, cfg: dict) -> None:
        """Index one collection."""
        print(f"[RAG Indexer] Collection: {name} ({cfg['description']})")

        if not self.force_reindex and self.collection_exists(name):
            existing = self.client.get_collection(name).count()
            print(f"  → Already indexed ({existing} chunks). Use --force to rebuild.\n")
            self._stats[name] = {"files": 0, "chunks": existing, "skipped": 0, "cached": True}
            return

        if self.force_reindex:
            try:
                self.client.delete_collection(name)
            except Exception:
                pass

        collection = self.client.get_or_create_collection(
            name=name,
            metadata={"hnsw:space": "cosine"},
        )

        # Gather files
        files = list(self._walk_files(cfg))
        print(f"  → Found {len(files)} files to index")

        if not files:
            print(f"  → WARNING: No files found. Check source_dirs exist in {self.source_dir}\n")
            self._stats[name] = {"files": 0, "chunks": 0, "skipped": 0, "cached": False}
            return

        # Process in batches
        all_docs, all_ids, all_metas = [], [], []
        file_count = 0
        skip_count = 0

        for path in files:
            chunks, metas = self._process_file(path, cfg)
            if not chunks:
                skip_count += 1
                continue
            for i, (chunk, meta) in enumerate(zip(chunks, metas)):
                chunk_id = self._make_id(path, i)
                all_docs.append(chunk)
                all_ids.append(chunk_id)
                all_metas.append(meta)
            file_count += 1

            # Flush batch
            if len(all_docs) >= BATCH_SIZE:
                self._flush_batch(collection, all_docs, all_ids, all_metas)
                all_docs, all_ids, all_metas = [], [], []

        # Flush remainder
        if all_docs:
            self._flush_batch(collection, all_docs, all_ids, all_metas)

        total_chunks = collection.count()
        print(f"  → Indexed {file_count} files → {total_chunks} chunks "
              f"(skipped {skip_count})\n")

        self._stats[name] = {
            "files": file_count,
            "chunks": total_chunks,
            "skipped": skip_count,
            "cached": False,
        }

    def _walk_files(self, cfg: dict) -> Iterator[Path]:
        """
        Yield all files matching the collection config.
        Applies extension filter + optional name_pattern filter.
        """
        extensions = cfg.get("extensions", set())
        name_patterns = [re.compile(p) for p in cfg.get("name_patterns", [])]
        source_dirs = cfg.get("source_dirs", [])

        for subdir in source_dirs:
            search_root = self.source_dir / subdir
            if not search_root.exists():
                logger.warning(f"Source subdir not found: {search_root}")
                continue

            for path in search_root.rglob("*"):
                if not path.is_file():
                    continue

                # Check size
                try:
                    size = path.stat().st_size
                    if size < MIN_FILE_BYTES or size > MAX_FILE_BYTES:
                        continue
                except OSError:
                    continue

                # Check extension match
                ext_match = path.suffix.lower() in extensions

                # Check name pattern match (for files without standard extensions)
                pattern_match = any(p.search(path.name) for p in name_patterns)

                if ext_match or pattern_match:
                    yield path

    def _process_file(self, path: Path, cfg: dict) -> tuple[list[str], list[dict]]:
        """
        Read file, clean it, split into overlapping chunks.
        Returns (chunks, metadatas) or ([], []) if file should be skipped.
        """
        try:
            text = path.read_text(encoding="utf-8", errors="ignore").strip()
        except Exception as e:
            logger.debug(f"Cannot read {path}: {e}")
            return [], []

        if not text:
            return [], []

        # Remove autogenerated boilerplate that adds noise
        text = self._clean_text(text)

        chunks = self._split_chunks(text)
        if not chunks:
            return [], []

        meta_base = {
            "file":       str(path),
            "filename":   path.name,
            "suffix":     path.suffix,
            "parent":     path.parent.name,
            "collection": cfg["description"],
        }

        metas = [{**meta_base, "chunk_index": i} for i in range(len(chunks))]
        return chunks, metas

    def _clean_text(self, text: str) -> str:
        """Remove noise from source files before indexing."""
        # Remove long license headers (common in AOSP)
        text = re.sub(
            r"/\*\s*Copyright.*?(?:limitations under the License\.)\s*\*/",
            "",
            text,
            flags=re.DOTALL | re.IGNORECASE,
        )
        # Remove pure comment-only lines in bulk
        lines = [l for l in text.splitlines() if not l.strip().startswith("//")]
        text = "\n".join(lines)
        # Collapse excessive blank lines
        text = re.sub(r"\n{3,}", "\n\n", text)
        return text.strip()

    def _split_chunks(self, text: str) -> list[str]:
        """
        Split text into overlapping word-based chunks.
        Overlap helps retrieval when relevant content spans chunk boundaries.
        """
        words = text.split()
        if not words:
            return []

        chunks = []
        step = CHUNK_SIZE_WORDS - CHUNK_OVERLAP_WORDS
        for i in range(0, len(words), step):
            chunk = " ".join(words[i : i + CHUNK_SIZE_WORDS])
            if chunk.strip():
                chunks.append(chunk)
        return chunks

    def _flush_batch(
        self,
        collection,
        docs: list[str],
        ids: list[str],
        metas: list[dict],
    ) -> None:
        """Embed and upsert a batch into ChromaDB."""
        embeddings = self.embedder.encode(
            docs,
            batch_size=64,
            show_progress_bar=False,
            normalize_embeddings=True,
        ).tolist()

        collection.upsert(
            documents=docs,
            embeddings=embeddings,
            ids=ids,
            metadatas=metas,
        )

    @staticmethod
    def _make_id(path: Path, chunk_index: int) -> str:
        """Stable, unique ID for a chunk — based on path hash + chunk index."""
        path_hash = hashlib.md5(str(path).encode()).hexdigest()[:12]
        return f"{path_hash}_{chunk_index}"

    def _print_summary(self, elapsed: float) -> None:
        print("=" * 60)
        print("[RAG Indexer] Indexing complete!")
        print(f"  Total time: {elapsed:.1f}s")
        print()
        total_chunks = 0
        for name, s in self._stats.items():
            status = "cached" if s.get("cached") else "indexed"
            print(f"  {name:<25} {s['chunks']:>6} chunks  [{status}]")
            total_chunks += s["chunks"]
        print(f"  {'TOTAL':<25} {total_chunks:>6} chunks")
        print("=" * 60)

    def _save_index_manifest(self) -> None:
        """Save a manifest file so we can verify index freshness later."""
        manifest = {
            "indexed_at": time.strftime("%Y-%m-%dT%H:%M:%S"),
            "embedding_model": EMBEDDING_MODEL,
            "collections": self._stats,
        }
        manifest_path = self.db_path / "index_manifest.json"
        manifest_path.write_text(json.dumps(manifest, indent=2))
        print(f"\n[RAG Indexer] Manifest saved → {manifest_path}")


# ─────────────────────────────────────────────────────────────────
# CLI entry point
# python -m rag.aosp_indexer --source aosp_source --db rag/chroma_db
# ─────────────────────────────────────────────────────────────────
if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO, format="%(levelname)s: %(message)s")

    parser = argparse.ArgumentParser(
        description="Build ChromaDB vector index from AOSP HAL source files"
    )
    parser.add_argument(
        "--source",
        default="aosp_source",
        help="Root directory of cloned AOSP repos (default: aosp_source)",
    )
    parser.add_argument(
        "--db",
        default="rag/chroma_db",
        help="ChromaDB storage path (default: rag/chroma_db)",
    )
    parser.add_argument(
        "--model",
        default=EMBEDDING_MODEL,
        help=f"SentenceTransformer model (default: {EMBEDDING_MODEL})",
    )
    parser.add_argument(
        "--force",
        action="store_true",
        help="Drop and rebuild existing collections",
    )
    args = parser.parse_args()

    indexer = AOSPIndexer(
        aosp_source_dir=args.source,
        db_path=args.db,
        embedding_model=args.model,
        force_reindex=args.force,
    )
    indexer.index()