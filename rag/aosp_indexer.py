"""
rag/aosp_indexer.py - STRONGEST HIDL EXCLUSION
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

# Collection definitions
COLLECTION_DEFS: dict[str, dict] = {
    "aosp_aidl": {"extensions": {".aidl"}, "source_dirs": ["hardware"], "description": "AIDL"},
    "aosp_cpp": {"extensions": {".cpp", ".h", ".cc"}, "source_dirs": ["hardware"], "description": "VHAL C++"},
    "aosp_build": {"extensions": {".bp"}, "source_dirs": ["hardware", "car"], "description": "Build"},
    "aosp_selinux": {"extensions": {".te"}, "source_dirs": ["sepolicy"], "description": "SELinux", "exclude_patterns": ["/prebuilts/api/"]},
    "aosp_vintf": {"extensions": {".xml", ".rc"}, "source_dirs": ["hardware"], "description": "VINTF", "name_patterns": [r"manifest.*\.xml$", r"compatibility_matrix.*\.xml$", r".*\.rc$"]},
    "aosp_car_api": {"extensions": {".kt", ".java"}, "source_dirs": ["car"], "description": "Car API"},
    "aosp_docs": {"extensions": {".md", ".rst", ".txt"}, "source_dirs": ["hardware", "car"], "description": "Docs"},
}

EMBEDDING_MODEL = "all-MiniLM-L6-v2"
CHUNK_SIZE_WORDS = 400
CHUNK_OVERLAP_WORDS = 50
BATCH_SIZE = 256
MIN_FILE_BYTES = 64
MAX_FILE_BYTES = 200_000

# ==================== STRONGEST EXCLUSION ====================
HIDL_EXCLUDE_PATTERNS = [
    "/2.0/", "/1.0/", "/3.0/", "/4.0/", "/hidl/", "/hidl-generated/",
    "V2_0", "V1_0", "V3_0", "@2.0", "@1.0", "@3.0", "vehicle@2", "vehicle@1"
]

HIDL_CONTENT_KEYWORDS = [
    "hidl::", "@2.0", "@1.0", "V2_0", "V1_0", "BpHw", "BnHw", "Hidl",
    "android.hardware.automotive.vehicle@", "IVehicle", "types.hidl"
]

# Block test/mock/fake files + known problematic files
HIDL_BAD_FILENAMES = {
    "test", "mock", "fake", "vts", "obd2", "composer", "virtualizer", 
    "mapper", "keymint", "identitycredential", "vehiclepropertystore", 
    "vehicleobjectpool", "accessforvehicleproperty"
}

class AOSPIndexer:
    def __init__(self, aosp_source_dir="aosp_source", db_path="rag/chroma_db", force_reindex=False):
        self.source_dir = Path(aosp_source_dir)
        self.db_path = Path(db_path)
        self.force_reindex = force_reindex
        self.db_path.mkdir(parents=True, exist_ok=True)

        self.client = chromadb.PersistentClient(path=str(self.db_path), settings=Settings(anonymized_telemetry=False))
        self.embedder = SentenceTransformer(EMBEDDING_MODEL)
        self._stats = {}

    def index(self):
        if not self.source_dir.exists():
            raise FileNotFoundError(f"Source not found: {self.source_dir}")

        print(f"\n[RAG Indexer] Starting indexing...")
        for name, cfg in COLLECTION_DEFS.items():
            self._index_collection(name, cfg)

        self._print_summary()
        self._save_index_manifest()

    def _index_collection(self, name, cfg):
        print(f"[Collection] {name} ({cfg['description']})")

        if not self.force_reindex and self.collection_exists(name):
            print("  → Already indexed\n")
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

        print(f"  → Indexed {file_count} files → {collection.count()} chunks\n")
        self._stats[name] = {"files": file_count, "chunks": collection.count(), "skipped": skip_count}

    def _walk_files(self, cfg):
        for subdir in cfg.get("source_dirs", []):
            root = self.source_dir / subdir
            if not root.exists(): continue

            for path in root.rglob("*"):
                if not path.is_file(): continue

                path_lower = path.as_posix().lower()
                fname_lower = path.name.lower()

                if any(pat.lower() in path_lower for pat in HIDL_EXCLUDE_PATTERNS):
                    continue

                if any(bad in fname_lower for bad in HIDL_BAD_FILENAMES):
                    continue

                try:
                    size = path.stat().st_size
                    if size < MIN_FILE_BYTES or size > MAX_FILE_BYTES:
                        continue
                except: continue

                ext_ok = path.suffix.lower() in cfg.get("extensions", set())
                name_ok = any(re.search(p, path.name) for p in cfg.get("name_patterns", []))

                if not (ext_ok or name_ok):
                    continue

                if self._contains_hidl_content(path):
                    continue

                yield path

    def _contains_hidl_content(self, path: Path) -> bool:
        try:
            text = path.read_text(encoding="utf-8", errors="ignore").lower()
            return any(kw in text for kw in HIDL_CONTENT_KEYWORDS)
        except:
            return False

    # === The rest of the methods (same as previous version) ===
    def _process_file(self, path: Path, cfg):
        try:
            text = path.read_text(encoding="utf-8", errors="ignore").strip()
        except:
            return [], []
        text = self._clean_text(text)
        chunks = self._split_chunks(text)
        if not chunks: return [], []
        meta = {"file": str(path), "filename": path.name, "suffix": path.suffix,
                "parent": path.parent.name, "collection": cfg["description"]}
        metas = [{**meta, "chunk_index": i} for i in range(len(chunks))]
        return chunks, metas

    def _clean_text(self, text):
        text = re.sub(r"/\*\s*Copyright.*?(?:License\.)\s*\*/", "", text, flags=re.DOTALL | re.IGNORECASE)
        lines = [l for l in text.splitlines() if not l.strip().startswith("//")]
        text = "\n".join(lines)
        return re.sub(r"\n{3,}", "\n\n", text).strip()

    def _split_chunks(self, text):
        words = text.split()
        if not words: return []
        chunks, step = [], CHUNK_SIZE_WORDS - CHUNK_OVERLAP_WORDS
        for i in range(0, len(words), step):
            chunk = " ".join(words[i:i + CHUNK_SIZE_WORDS])
            if chunk.strip(): chunks.append(chunk)
        return chunks

    def _flush_batch(self, collection, docs, ids, metas):
        embeddings = self.embedder.encode(docs, batch_size=64, show_progress_bar=False, normalize_embeddings=True).tolist()
        collection.upsert(documents=docs, embeddings=embeddings, ids=ids, metadatas=metas)

    @staticmethod
    def _make_id(path, i):
        return hashlib.md5(str(path).encode()).hexdigest()[:12] + f"_{i}"

    def collection_exists(self, name):
        try:
            return self.client.get_collection(name).count() > 0
        except:
            return False

    def _print_summary(self):
        print("=" * 60)
        print("[RAG Indexer] Indexing complete!")
        total = sum(s.get("chunks", 0) for s in self._stats.values())
        for name, s in self._stats.items():
            print(f"  {name:<20} {s.get('chunks',0):>6} chunks")
        print(f"  TOTAL               {total:>6} chunks")
        print("=" * 60)

    def _save_index_manifest(self):
        (self.db_path / "index_manifest.json").write_text(json.dumps({
            "indexed_at": time.strftime("%Y-%m-%d %H:%M"),
            "collections": self._stats
        }, indent=2))


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO, format="%(levelname)s: %(message)s")
    parser = argparse.ArgumentParser()
    parser.add_argument("--source", default="aosp_source")
    parser.add_argument("--db", default="rag/chroma_db")
    parser.add_argument("--force", action="store_true")
    args = parser.parse_args()

    indexer = AOSPIndexer(args.source, args.db, args.force)
    indexer.index()