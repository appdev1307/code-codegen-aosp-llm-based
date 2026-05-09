"""
rag/aosp_indexer.py - AGGRESSIVE HIDL EXCLUSION
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

# Collection definitions (same as before)
COLLECTION_DEFS: dict[str, dict] = {
    "aosp_aidl": {"extensions": {".aidl"}, "source_dirs": ["hardware"], "description": "AIDL interface definitions"},
    "aosp_cpp": {"extensions": {".cpp", ".h", ".cc"}, "source_dirs": ["hardware"], "description": "VHAL C++ implementation files"},
    "aosp_build": {"extensions": {".bp"}, "source_dirs": ["hardware", "car"], "description": "Android.bp build files"},
    "aosp_selinux": {
        "extensions": {".te"}, "source_dirs": ["sepolicy"], "description": "SELinux policy files",
        "name_patterns": [r"file_contexts$", r"property_contexts$", r"service_contexts$", r"hwservice_contexts$"],
        "exclude_patterns": ["/prebuilts/api/"],
    },
    "aosp_vintf": {
        "extensions": {".xml", ".rc"}, "source_dirs": ["hardware"], "description": "VINTF manifest and init.rc files",
        "name_patterns": [r"manifest.*\.xml$", r"compatibility_matrix.*\.xml$", r".*\.rc$"],
    },
    "aosp_car_api": {"extensions": {".kt", ".java"}, "source_dirs": ["car"], "description": "Car API Kotlin/Java"},
    "aosp_docs": {"extensions": {".md", ".rst", ".txt"}, "source_dirs": ["hardware", "car"], "description": "Docs"},
}

EMBEDDING_MODEL = "all-MiniLM-L6-v2"
CHUNK_SIZE_WORDS = 400
CHUNK_OVERLAP_WORDS = 50
BATCH_SIZE = 256
MIN_FILE_BYTES = 64
MAX_FILE_BYTES = 200_000

# ==================== AGGRESSIVE HIDL EXCLUSION ====================
HIDL_EXCLUDE_PATTERNS = [
    "/2.0/", "/1.0/", "/3.0/", "/4.0/", "/hidl/", "/hidl-generated/",
    "V2_0", "V1_0", "V3_0", "@2.0", "@1.0", "@3.0",
    "vehicle@2", "vehicle@1", "hidl::"
]

HIDL_CONTENT_KEYWORDS = [
    "hidl::", "@2.0", "@1.0", "V2_0", "V1_0", "BpHw", "BnHw", "Hidl",
    "android.hardware.automotive.vehicle@", "IVehicle", "types.hidl"
]

class AOSPIndexer:
    def __init__(self, aosp_source_dir: str | Path = "aosp_source", db_path: str | Path = "rag/chroma_db",
                 embedding_model: str = EMBEDDING_MODEL, force_reindex: bool = False):
        self.source_dir = Path(aosp_source_dir)
        self.db_path = Path(db_path)
        self.force_reindex = force_reindex
        self.db_path.mkdir(parents=True, exist_ok=True)

        self.client = chromadb.PersistentClient(path=str(self.db_path), settings=Settings(anonymized_telemetry=False))
        self.embedder = SentenceTransformer(embedding_model)
        self._stats: dict[str, dict] = {}

    def index(self):
        if not self.source_dir.exists():
            raise FileNotFoundError(f"AOSP source not found: {self.source_dir}")

        print(f"\n[RAG Indexer] Starting indexing...")
        for name, cfg in COLLECTION_DEFS.items():
            self._index_collection(name, cfg)

        self._print_summary()
        self._save_index_manifest()

    def _index_collection(self, name: str, cfg: dict):
        print(f"[Collection] {name} ({cfg['description']})")

        if not self.force_reindex and self.collection_exists(name):
            print("  → Already indexed (use --force to rebuild)\n")
            return

        if self.force_reindex:
            try: self.client.delete_collection(name)
            except: pass

        collection = self.client.get_or_create_collection(name=name, metadata={"hnsw:space": "cosine"})

        files = list(self._walk_files(cfg))
        print(f"  → Found {len(files)} files")

        all_docs, all_ids, all_metas = [], [], []
        file_count = skip_count = 0

        for path in files:
            chunks, metas = self._process_file(path, cfg)
            if not chunks:
                skip_count += 1
                continue
            for i, (chunk, meta) in enumerate(zip(chunks, metas)):
                all_docs.append(chunk)
                all_ids.append(self._make_id(path, i))
                all_metas.append(meta)
            file_count += 1

            if len(all_docs) >= BATCH_SIZE:
                self._flush_batch(collection, all_docs, all_ids, all_metas)
                all_docs, all_ids, all_metas = [], [], []

        if all_docs:
            self._flush_batch(collection, all_docs, all_ids, all_metas)

        print(f"  → Indexed {file_count} files → {collection.count()} chunks (skipped {skip_count})\n")

    def _walk_files(self, cfg: dict) -> Iterator[Path]:
        for subdir in cfg.get("source_dirs", []):
            root = self.source_dir / subdir
            if not root.exists(): continue

            for path in root.rglob("*"):
                if not path.is_file(): continue

                path_lower = path.as_posix().lower()

                if any(pat.lower() in path_lower for pat in HIDL_EXCLUDE_PATTERNS):
                    continue

                if any(pat in str(path) for pat in cfg.get("exclude_patterns", [])):
                    continue

                try:
                    size = path.stat().st_size
                    if size < MIN_FILE_BYTES or size > MAX_FILE_BYTES:
                        continue
                except: continue

                ext_match = path.suffix.lower() in cfg.get("extensions", set())
                name_match = any(re.search(p, path.name) for p in cfg.get("name_patterns", []))

                if not (ext_match or name_match):
                    continue

                # Aggressive content check
                if self._contains_hidl_content(path):
                    continue

                yield path

    def _contains_hidl_content(self, path: Path) -> bool:
        try:
            text = path.read_text(encoding="utf-8", errors="ignore").lower()
            hits = [kw for kw in HIDL_CONTENT_KEYWORDS if kw.lower() in text]
            if len(hits) >= 1:   # Lowered threshold to 1
                return True
        except:
            pass
        return False

    def _process_file(self, path: Path, cfg: dict):
        try:
            text = path.read_text(encoding="utf-8", errors="ignore").strip()
        except:
            return [], []

        text = self._clean_text(text)
        chunks = self._split_chunks(text)
        if not chunks:
            return [], []

        meta_base = {"file": str(path), "filename": path.name, "suffix": path.suffix,
                     "parent": path.parent.name, "collection": cfg["description"]}
        metas = [{**meta_base, "chunk_index": i} for i in range(len(chunks))]
        return chunks, metas

    def _clean_text(self, text: str) -> str:
        text = re.sub(r"/\*\s*Copyright.*?(?:License\.)\s*\*/", "", text, flags=re.DOTALL | re.IGNORECASE)
        lines = [l for l in text.splitlines() if not l.strip().startswith("//")]
        text = "\n".join(lines)
        return re.sub(r"\n{3,}", "\n\n", text).strip()

    def _split_chunks(self, text: str) -> list[str]:
        words = text.split()
        if not words: return []
        chunks = []
        step = CHUNK_SIZE_WORDS - CHUNK_OVERLAP_WORDS
        for i in range(0, len(words), step):
            chunk = " ".join(words[i:i + CHUNK_SIZE_WORDS])
            if chunk.strip():
                chunks.append(chunk)
        return chunks

    def _flush_batch(self, collection, docs, ids, metas):
        embeddings = self.embedder.encode(docs, batch_size=64, show_progress_bar=False, normalize_embeddings=True).tolist()
        collection.upsert(documents=docs, embeddings=embeddings, ids=ids, metadatas=metas)

    @staticmethod
    def _make_id(path: Path, chunk_index: int) -> str:
        return hashlib.md5(str(path).encode()).hexdigest()[:12] + f"_{chunk_index}"

    def collection_exists(self, name: str) -> bool:
        try:
            return self.client.get_collection(name).count() > 0
        except:
            return False

    def _print_summary(self):
        print("=" * 60)
        print("[RAG Indexer] Indexing complete!")
        total = sum(s.get("chunks", 0) for s in self._stats.values())
        print(f"  TOTAL {total} chunks")
        print("=" * 60)

    def _save_index_manifest(self):
        manifest_path = self.db_path / "index_manifest.json"
        manifest_path.write_text(json.dumps({"indexed_at": time.strftime("%Y-%m-%dT%H:%M:%S")}, indent=2))


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO, format="%(levelname)s: %(message)s")
    parser = argparse.ArgumentParser()
    parser.add_argument("--source", default="aosp_source")
    parser.add_argument("--db", default="rag/chroma_db")
    parser.add_argument("--force", action="store_true")
    args = parser.parse_args()

    indexer = AOSPIndexer(aosp_source_dir=args.source, db_path=args.db, force_reindex=args.force)
    indexer.index()