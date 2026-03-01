"""
agents/rag_dspy_architect_agent.py
═══════════════════════════════════════════════════════════════════
RAG+DSPy drop-in replacement for ArchitectAgent (condition 3).

The ArchitectAgent is the top-level HAL module orchestrator — it
coordinates AIDL, C++, SELinux, and Build sub-agents for one module.

In condition 3 each sub-agent is itself a RAG+DSPy variant, so this
class mainly:
  1. Accepts the same ModuleSpec interface as the original
  2. Instantiates all RAG+DSPy sub-agents with shared config
  3. Runs them in the same order as the original ArchitectAgent
  4. Logs per-sub-agent scores for thesis metrics

Called from multi_main_rag_dspy.py → _generate_one_module()
    agent = RAGDSPyArchitectAgent(dspy_programs_dir=..., rag_top_k=...)
    agent.run(module_spec)
═══════════════════════════════════════════════════════════════════
"""

from __future__ import annotations

import time
from agents.rag_dspy_mixin       import RAGDSPyMixin
from agents.rag_dspy_aidl_agent  import RAGDSPyAIDLAgent
from agents.rag_dspy_cpp_agent   import RAGDSPyCppAgent
from agents.rag_dspy_selinux_agent import RAGDSPySELinuxAgent
from agents.rag_dspy_build_agent import RAGDSPyBuildAgent


class RAGDSPyArchitectAgent:
    """
    Orchestrates all HAL-layer RAG+DSPy sub-agents for one module.

    Equivalent to ArchitectAgent but every sub-agent uses
    RAG retrieval + DSPy-optimised prompts.

    Parameters
    ----------
    dspy_programs_dir : str
        Root dir containing saved optimised DSPy programs.
    rag_top_k : int
        AOSP chunks retrieved per sub-agent call.
    rag_db_path : str
        ChromaDB path.
    output_root : str
        Directory where generated files are written.
        Defaults to "output" (C1/C2 behaviour).
    """

    def __init__(
        self,
        dspy_programs_dir: str = "dspy_opt/saved",
        rag_top_k:         int = 3,
        rag_db_path:       str = "rag/chroma_db",
        output_root:       str = "output",
    ):
        self._cfg = dict(
            dspy_programs_dir=dspy_programs_dir,
            rag_top_k=rag_top_k,
            rag_db_path=rag_db_path,
            output_root=output_root,
        )

    def run(self, module_spec) -> dict[str, str]:
        """
        Run all HAL-layer sub-agents for module_spec.

        Parameters
        ----------
        module_spec : ModuleSpec
            Same object passed to the original ArchitectAgent.run()

        Returns
        -------
        dict mapping sub-agent name → generated code string
        """
        domain  = module_spec.domain
        results = {}

        print(f"\n  [RAG+DSPy ARCHITECT] Running sub-agents for module: {domain}")
        t_start = time.time()

        sub_agents = [
            ("AIDL",    RAGDSPyAIDLAgent,    lambda a: a.run(module_spec)),
            ("CPP",     RAGDSPyCppAgent,     lambda a: a.run(module_spec)),
            ("SELinux", RAGDSPySELinuxAgent, lambda a: a.run(module_spec)),
            ("Build",   RAGDSPyBuildAgent,   lambda a: a.run(module_spec)),
        ]

        for name, AgentClass, runner in sub_agents:
            t0 = time.time()
            try:
                agent  = AgentClass(**self._cfg)
                output = runner(agent)
                elapsed = time.time() - t0
                results[name] = output or ""
                status = "✓" if output else "⚠ empty"
                print(f"  [RAG+DSPy ARCHITECT] {name:<8} → {status} ({elapsed:.1f}s)")
            except Exception as e:
                elapsed = time.time() - t0
                results[name] = ""
                print(f"  [RAG+DSPy ARCHITECT] {name:<8} → FAILED ({elapsed:.1f}s): {e}")

        total = time.time() - t_start
        ok    = sum(1 for v in results.values() if v)
        print(
            f"  [RAG+DSPy ARCHITECT] {domain} complete — "
            f"{ok}/{len(sub_agents)} sub-agents OK ({total:.1f}s)"
        )
        return results