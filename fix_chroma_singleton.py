#!/usr/bin/env python3
"""
fix_chroma_singleton.py
───────────────────────
Fixes the ChromaDB "An instance of Chroma already exists for rag/chroma_db
with different settings" error in the RAG+DSPy pipeline.

The Problem:
  Multiple agents (aidl, cpp, selinux, etc.) each try to instantiate their
  own ChromaDB client pointing to the same persist_directory but with
  slightly different Settings objects. ChromaDB enforces a singleton per
  directory, so the second agent fails.

The Fix:
  Create a shared RAG singleton module that ALL agents import. The client
  is initialised once and reused.

Usage:
  1. Copy this file to your project root as `rag_singleton.py`
  2. In each agent that uses RAG, replace the local ChromaDB init with:
       from rag_singleton import get_rag_context
  3. Call: context = get_rag_context(query, top_k=8)

If you'd rather patch in-place without restructuring, see the
monkey-patch approach at the bottom of this file.
"""

from __future__ import annotations

import os
import threading
from pathlib import Path
from typing import Optional

# ── Singleton ChromaDB Client ─────────────────────────────────────

_client = None
_collection = None
_lock = threading.Lock()
_CHROMA_DIR = os.environ.get(
    "CHROMA_PERSIST_DIR",
    str(Path(__file__).parent / "rag" / "chroma_db")
)
_COLLECTION_NAME = os.environ.get("CHROMA_COLLECTION", "aosp_docs")


def _init_client():
    """Initialise ChromaDB client exactly once."""
    global _client, _collection
    if _client is not None:
        return

    with _lock:
        if _client is not None:
            return  # double-check after acquiring lock

        try:
            import chromadb
            from chromadb.config import Settings

            _client = chromadb.Client(Settings(
                chroma_db_impl="duckdb+parquet",
                persist_directory=_CHROMA_DIR,
                anonymized_telemetry=False,
            ))
            _collection = _client.get_collection(_COLLECTION_NAME)
            print(f"[RAG] ChromaDB singleton initialised: "
                  f"{_collection.count()} documents in '{_COLLECTION_NAME}'")

        except Exception as e:
            # Try newer chromadb API (>=0.4.x)
            try:
                import chromadb

                _client = chromadb.PersistentClient(path=_CHROMA_DIR)
                _collection = _client.get_collection(_COLLECTION_NAME)
                print(f"[RAG] ChromaDB singleton initialised (PersistentClient): "
                      f"{_collection.count()} documents in '{_COLLECTION_NAME}'")

            except Exception as e2:
                print(f"[RAG] WARNING: ChromaDB init failed: {e2}")
                _client = "FAILED"
                _collection = None


def get_rag_context(query: str, top_k: int = 8) -> str:
    """Query the AOSP document collection. Returns concatenated context string.

    Thread-safe. Returns empty string on failure (graceful degradation).
    """
    _init_client()

    if _collection is None:
        return ""

    try:
        results = _collection.query(
            query_texts=[query],
            n_results=top_k,
        )
        documents = results.get("documents", [[]])[0]
        if not documents:
            return ""

        # Format context with source markers
        context_parts = []
        metadatas = results.get("metadatas", [[]])[0]
        for i, (doc, meta) in enumerate(zip(documents, metadatas or [{}] * len(documents))):
            source = meta.get("source", f"doc_{i}")
            context_parts.append(f"--- AOSP Source: {source} ---\n{doc}")

        return "\n\n".join(context_parts)

    except Exception as e:
        print(f"[RAG] Query failed: {e}")
        return ""


def get_collection():
    """Get the raw ChromaDB collection for direct queries."""
    _init_client()
    return _collection


# ══════════════════════════════════════════════════════════════════
# MONKEY-PATCH APPROACH (alternative — no restructuring needed)
# ══════════════════════════════════════════════════════════════════
# If you don't want to change agent imports, add this at the TOP of
# multi_main_rag_dspy.py BEFORE any agent imports:
#
#   import fix_chroma_singleton
#   fix_chroma_singleton.patch_chromadb()
#
# This forces all chromadb.Client() and PersistentClient() calls
# to return the same singleton instance.

def patch_chromadb():
    """Monkey-patch chromadb to return a singleton client.
    Call ONCE at the start of multi_main_rag_dspy.py.
    """
    _init_client()

    if _client is None or _client == "FAILED":
        print("[RAG] WARNING: patch_chromadb() — client init failed, RAG disabled")
        return

    import chromadb

    _original_client = chromadb.Client
    _original_persistent = getattr(chromadb, "PersistentClient", None)

    def _patched_client(*args, **kwargs):
        return _client

    def _patched_persistent(*args, **kwargs):
        return _client

    chromadb.Client = _patched_client
    if _original_persistent:
        chromadb.PersistentClient = _patched_persistent

    print("[RAG] ChromaDB monkey-patched — all agents will share singleton client")


# ══════════════════════════════════════════════════════════════════
# SELF-TEST
# ══════════════════════════════════════════════════════════════════

if __name__ == "__main__":
    print("Testing RAG singleton...")
    ctx = get_rag_context("VehiclePropValue AIDL interface", top_k=3)
    if ctx:
        print(f"Retrieved {len(ctx)} chars of context")
        print(ctx[:500])
    else:
        print("No context retrieved (collection may be empty or uninitialised)")
