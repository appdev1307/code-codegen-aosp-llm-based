"""
RAG (Retrieval-Augmented Generation) layer for AOSP HAL code generation.

Provides two main components:
  - AOSPIndexer:   crawls AOSP source files and builds a ChromaDB vector index (run once)
  - AOSPRetriever: queries the index at generation time to retrieve relevant examples

Usage:
    # Step 1 — build index once
    from rag.aosp_indexer import AOSPIndexer
    AOSPIndexer(aosp_source_dir="aosp_source").index()

    # Step 2 — retrieve at generation time
    from rag.aosp_retriever import AOSPRetriever
    retriever = AOSPRetriever()
    results = retriever.retrieve("ABS IsEnabled boolean ADAS HAL", agent_type="aidl")
    context  = retriever.format_for_prompt(results)
"""

from rag.aosp_indexer import AOSPIndexer
from rag.aosp_retriever import AOSPRetriever, COLLECTION_MAP

__all__ = ["AOSPIndexer", "AOSPRetriever", "COLLECTION_MAP"]