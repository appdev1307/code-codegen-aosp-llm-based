"""
agents/rag_dspy_mixin.py
═══════════════════════════════════════════════════════════════════
Shared base mixin for all RAG+DSPy generation agents.

Every RAG+DSPy agent inherits this mixin alongside its original
adaptive agent parent. The mixin adds two capabilities:

  1. RAG  — retrieves relevant AOSP source chunks from ChromaDB
             before every generation call and injects them as
             grounding context into the prompt

  2. DSPy — uses a MIPROv2-optimised prompt program instead of
             the hand-crafted prompt variants from condition 2

Usage pattern (in every rag_dspy_*.py agent file):

    class RAGDSPyAIDLAgent(RAGDSPyMixin, VHALAIDLAgent):
        AGENT_TYPE        = "aidl"          # collection key in COLLECTION_MAP
        DSPY_OUTPUT_FIELD = "aidl_code"     # output field on dspy.Prediction

        def __init__(self, **kwargs):
            VHALAIDLAgent.__init__(self)
            self._init_rag_dspy(**kwargs)

        def run(self, module_spec):
            query   = self._build_query(module_spec)
            context = self._retrieve(query)
            return self._generate(
                domain       = module_spec.domain,
                properties   = module_spec.to_llm_spec(),
                aosp_context = context,
            )

The mixin is deliberately thin — it does NOT override `run()`.
Each agent subclass implements its own `run()` to control exactly
what inputs go to the DSPy module, because each agent has a
different Signature (different InputFields).
═══════════════════════════════════════════════════════════════════
"""

from __future__ import annotations

import logging
import time
from pathlib import Path
from typing import Optional

logger = logging.getLogger(__name__)


class RAGDSPyMixin:
    """
    Mixin that adds RAG retrieval + DSPy optimised generation to any agent.

    Class-level attributes to set in every subclass:
        AGENT_TYPE        : str   — key into COLLECTION_MAP and MODULE_REGISTRY
        DSPY_OUTPUT_FIELD : str   — attribute name on dspy.Prediction to return

    Instance methods provided:
        _init_rag_dspy(dspy_programs_dir, rag_top_k, rag_db_path)
            Call from __init__() after the parent agent is initialised.

        _retrieve(query) -> str
            Retrieve AOSP context chunks for a query string.
            Returns formatted string ready for prompt injection.
            Returns "" if RAG is unavailable.

        _generate(**kwargs) -> str
            Run the DSPy module forward pass.
            kwargs must match the InputFields of this agent's Signature.
            Returns the output field as a plain string.
            Returns "" on failure.

        _score(prediction) -> float
            Score the prediction using this agent's metric function.
            Useful for logging and thesis metrics collection.

        _log(msg)
            Standardised prefixed logging for this agent type.
    """

    # ── Must be set by every subclass ───────────────────────────
    AGENT_TYPE:        str = ""
    DSPY_OUTPUT_FIELD: str = ""

    # ─────────────────────────────────────────────────────────────
    # Initialisation
    # ─────────────────────────────────────────────────────────────

    def _init_rag_dspy(
        self,
        dspy_programs_dir: str  = "dspy_opt/saved",
        rag_top_k:         int  = 3,
        rag_db_path:       str  = "rag/chroma_db",
    ) -> None:
        """
        Initialise RAG retriever and DSPy module for this agent.

        Parameters
        ----------
        dspy_programs_dir : str
            Root directory containing saved optimised DSPy programs.
            Each agent looks for <dir>/<AGENT_TYPE>_program/program.json
        rag_top_k : int
            Number of AOSP source chunks to retrieve per query.
        rag_db_path : str
            Path to the ChromaDB vector database built by AOSPIndexer.
        """
        assert self.AGENT_TYPE,        f"{self.__class__.__name__} must set AGENT_TYPE"
        assert self.DSPY_OUTPUT_FIELD, f"{self.__class__.__name__} must set DSPY_OUTPUT_FIELD"

        self._rag_top_k        = rag_top_k
        self._rag_retriever    = None
        self._dspy_module      = None
        self._dspy_optimised   = False
        self._metric_fn        = None

        # ── Load RAG retriever ──────────────────────────────────
        try:
            from rag.aosp_retriever import get_retriever
            retriever = get_retriever(db_path=rag_db_path)
            if retriever.is_ready():
                self._rag_retriever = retriever
                self._log("RAG retriever ready ✓")
            else:
                self._log("WARNING: RAG index empty — generating without context")
        except Exception as e:
            self._log(f"WARNING: RAG not available ({e}) — generating without context")

        # ── Load DSPy module ────────────────────────────────────
        try:
            from dspy_opt.hal_modules import get_module
            import dspy

            # Connect DSPy to the local Ollama LLM if not already configured
            self._ensure_dspy_configured()

            module = get_module(
                self.AGENT_TYPE,
                programs_dir=dspy_programs_dir,
                auto_load=True,
            )
            self._dspy_module    = module
            self._dspy_optimised = module.is_optimised
            status = "optimised ✓" if self._dspy_optimised else "unoptimised (fallback)"
            self._log(f"DSPy module loaded [{status}]")
        except Exception as e:
            self._log(f"WARNING: DSPy module not available ({e})")

        # ── Load metric function ────────────────────────────────
        try:
            from dspy_opt.metrics import METRIC_REGISTRY
            self._metric_fn = METRIC_REGISTRY.get(self.AGENT_TYPE)
        except Exception:
            self._metric_fn = None

    # ─────────────────────────────────────────────────────────────
    # RAG retrieval
    # ─────────────────────────────────────────────────────────────

    def _retrieve(self, query: str) -> str:
        """
        Retrieve relevant AOSP source chunks for the given query.

        Returns a formatted string ready to inject into the LLM prompt,
        or "" if RAG is unavailable or returns no relevant results.

        Parameters
        ----------
        query : str
            Natural language description of what is being generated.
            Example: "ADAS ABS IsEnabled boolean READ_WRITE VHAL AIDL interface"
        """
        if self._rag_retriever is None:
            return ""

        try:
            t0 = time.time()
            results = self._rag_retriever.retrieve(
                query,
                agent_type=self.AGENT_TYPE,
                top_k=self._rag_top_k,
            )
            context  = self._rag_retriever.format_for_prompt(
                results,
                label=f"{self.AGENT_TYPE.upper()} AOSP Reference",
            )
            elapsed  = time.time() - t0
            n        = len(results)
            avg_score = (
                round(sum(r["score"] for r in results) / n, 3)
                if results else 0.0
            )
            self._log(
                f"RAG: retrieved {n} chunks "
                f"(avg_score={avg_score}, {elapsed:.2f}s)"
            )
            return context
        except Exception as e:
            self._log(f"RAG retrieval failed: {e}")
            return ""

    def _retrieve_multi(self, queries: list[str]) -> str:
        """
        Retrieve and deduplicate results across multiple queries.
        Use when a module has diverse signal types that benefit from
        multiple targeted queries.

        Parameters
        ----------
        queries : list[str]  — one query per signal type or sub-component
        """
        if self._rag_retriever is None:
            return ""
        try:
            results = self._rag_retriever.retrieve_multi(
                queries,
                agent_type=self.AGENT_TYPE,
                top_k=self._rag_top_k,
            )
            return self._rag_retriever.format_for_prompt(
                results,
                label=f"{self.AGENT_TYPE.upper()} AOSP Reference (multi-query)",
            )
        except Exception as e:
            self._log(f"RAG multi-retrieve failed: {e}")
            return ""

    # ─────────────────────────────────────────────────────────────
    # DSPy generation
    # ─────────────────────────────────────────────────────────────

    def _generate(self, **kwargs) -> str:
        """
        Run the DSPy module forward pass and return the output as a string.

        kwargs must match the InputFields of this agent's Signature.
        Returns "" on any failure — callers should handle empty strings
        by falling back to their parent agent's original generation method.

        Parameters
        ----------
        **kwargs : dict
            InputField values, e.g.:
                domain="ADAS", properties="...", aosp_context="..."
        """
        if self._dspy_module is None:
            self._log("DSPy module not available — returning empty")
            return ""

        try:
            t0      = time.time()
            result  = self._dspy_module(**kwargs)
            elapsed = time.time() - t0
            output  = getattr(result, self.DSPY_OUTPUT_FIELD, "") or ""

            if not output.strip():
                self._log(f"WARNING: DSPy returned empty output ({elapsed:.1f}s)")
                return ""

            self._log(
                f"DSPy generated {len(output)} chars "
                f"({'optimised' if self._dspy_optimised else 'unoptimised'}, "
                f"{elapsed:.1f}s)"
            )
            return output

        except Exception as e:
            self._log(f"DSPy generation failed: {e}")
            return ""

    # ─────────────────────────────────────────────────────────────
    # Scoring
    # ─────────────────────────────────────────────────────────────

    def _score(self, prediction, example=None) -> float:
        """
        Score a prediction using this agent's registered metric function.
        Returns 0.0 if no metric is registered or scoring fails.

        Parameters
        ----------
        prediction : dspy.Prediction or any object with output field
        example    : optional dspy.Example with inputs for coverage scoring
        """
        if self._metric_fn is None:
            return 0.0
        try:
            return self._metric_fn(example, prediction)
        except Exception as e:
            self._log(f"Scoring failed: {e}")
            return 0.0

    # ─────────────────────────────────────────────────────────────
    # Helpers
    # ─────────────────────────────────────────────────────────────

    def _log(self, msg: str) -> None:
        """Standardised log prefix for this agent."""
        tag = self.AGENT_TYPE.upper() if self.AGENT_TYPE else self.__class__.__name__
        print(f"  [RAG+DSPy {tag}] {msg}")

    @staticmethod
    def _ensure_dspy_configured() -> None:
        """
        Configure DSPy LM if not already done.
        Uses the same local Ollama endpoint as the rest of the pipeline.
        Safe to call multiple times — skips if already configured.
        """
        import dspy
        try:
            # Check if LM is already configured by trying to access it
            _ = dspy.settings.lm
            if dspy.settings.lm is not None:
                return
        except AttributeError:
            pass

        lm = dspy.LM(
            "ollama/qwen2.5-coder:32b",
            api_base="http://localhost:11434",
            cache=False,
        )
        dspy.configure(lm=lm)
        logger.info("[RAGDSPyMixin] DSPy configured with qwen2.5-coder:32b")

    @property
    def rag_available(self) -> bool:
        """True if RAG retriever is ready to serve queries."""
        return self._rag_retriever is not None

    @property
    def dspy_available(self) -> bool:
        """True if DSPy module is loaded and ready."""
        return self._dspy_module is not None

    @property
    def is_fully_ready(self) -> bool:
        """True if both RAG and DSPy are available."""
        return self.rag_available and self.dspy_available