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
  4. Writes each agent's output to output_root (isolated C3 folder)
  5. Logs per-sub-agent scores for thesis metrics

Called from multi_main_rag_dspy.py → _generate_one_module()
    agent = RAGDSPyArchitectAgent(dspy_programs_dir=..., rag_top_k=...,
                                   output_root="output_rag_dspy")
    agent.run(module_spec)
═══════════════════════════════════════════════════════════════════
"""

from __future__ import annotations

import time
from pathlib import Path

from agents.rag_dspy_mixin         import RAGDSPyMixin
from agents.rag_dspy_aidl_agent    import RAGDSPyAIDLAgent
from agents.rag_dspy_cpp_agent     import RAGDSPyCppAgent
from agents.rag_dspy_selinux_agent import RAGDSPySELinuxAgent
from agents.rag_dspy_build_agent   import RAGDSPyBuildAgent


class RAGDSPyArchitectAgent:
    """
    Orchestrates all HAL-layer RAG+DSPy sub-agents for one module.

    Equivalent to ArchitectAgent but every sub-agent uses
    RAG retrieval + DSPy-optimised prompts.  Generated code is
    written to output_root (default: output_rag_dspy/) — isolated
    from C1 (output/) and C2 (output_adaptive/).

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
        Must be "output_rag_dspy" for condition 3 isolation.
    """

    # Path templates relative to output_root — mirrors C1 structure
    # so the same _score_files() globs work across all conditions.
    _HAL_BASE   = "hardware/interfaces/automotive/vehicle"
    _AIDL_DIR   = _HAL_BASE + "/aidl/android/hardware/automotive/vehicle"
    _CPP_DIR    = _HAL_BASE + "/impl"
    _SELINUX_DIR = "sepolicy"
    _BUILD_DIR  = _HAL_BASE + "/impl"   # Android.bp lives alongside .cpp

    def __init__(
        self,
        dspy_programs_dir: str = "dspy_opt/saved",
        rag_top_k:         int = 3,
        rag_db_path:       str = "rag/chroma_db",
        output_root:       str = "output_rag_dspy",
    ):
        # Config for HAL agents — they only take these 3 params
        self._cfg_base = dict(
            dspy_programs_dir=dspy_programs_dir,
            rag_top_k=rag_top_k,
            rag_db_path=rag_db_path,
        )
        self._output_root = Path(output_root)

    # ─────────────────────────────────────────────────────────────
    # File writing helpers
    # ─────────────────────────────────────────────────────────────

    def _write(self, rel_path: str, content: str) -> Path:
        """Write content to output_root/rel_path, creating dirs as needed."""
        target = self._output_root / rel_path
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(content, encoding="utf-8")
        return target

    def _write_aidl(self, domain: str, content: str) -> list[Path]:
        """
        Write AIDL output.  The agent may return one combined file or
        multiple files separated by // FILE: <path> markers.
        Falls back to a single VehicleProperty<Domain>.aidl file.
        """
        written = []
        domain_cap = domain.capitalize()

        # Try to split on // FILE: <path> markers if the model emitted them
        if "// FILE:" in content:
            current_path = None
            buf = []
            for line in content.splitlines():
                if line.startswith("// FILE:"):
                    if current_path and buf:
                        written.append(self._write(current_path, "\n".join(buf)))
                        buf = []
                    current_path = line.replace("// FILE:", "").strip()
                else:
                    buf.append(line)
            if current_path and buf:
                written.append(self._write(current_path, "\n".join(buf)))
        
        if not written:
            # Single file fallback
            fname = f"VehicleProperty{domain_cap}.aidl"
            written.append(self._write(f"{self._AIDL_DIR}/{fname}", content))

        return written

    def _write_cpp(self, domain: str, content: str) -> list[Path]:
        """Write C++ implementation file."""
        domain_cap = domain.capitalize()
        fname = f"VehicleHalService{domain_cap}.cpp"
        return [self._write(f"{self._CPP_DIR}/{fname}", content)]

    def _write_selinux(self, domain: str, content: str) -> list[Path]:
        """Write SELinux .te policy file."""
        domain_lower = domain.lower()
        # Try to split on // FILE: markers (agent may emit multiple .te files)
        written = []
        if "// FILE:" in content:
            current_path = None
            buf = []
            for line in content.splitlines():
                if line.startswith("// FILE:"):
                    if current_path and buf:
                        written.append(self._write(current_path, "\n".join(buf)))
                        buf = []
                    current_path = line.replace("// FILE:", "").strip()
                else:
                    buf.append(line)
            if current_path and buf:
                written.append(self._write(current_path, "\n".join(buf)))

        if not written:
            fname = f"vehicle_hal_{domain_lower}.te"
            written.append(self._write(f"{self._SELINUX_DIR}/{fname}", content))

        return written

    def _write_build(self, domain: str, content: str) -> list[Path]:
        """Write Android.bp build file."""
        return [self._write(f"{self._BUILD_DIR}/Android.bp", content)]

    # ─────────────────────────────────────────────────────────────
    # Orchestration
    # ─────────────────────────────────────────────────────────────

    def run(self, module_spec) -> dict[str, str]:
        """
        Run all HAL-layer sub-agents for module_spec and write their
        output to output_root.

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
        print(f"  [RAG+DSPy ARCHITECT] Output → {self._output_root.resolve()}")
        t_start = time.time()

        sub_agents = [
            ("AIDL",    RAGDSPyAIDLAgent,    lambda a: a.run(module_spec),
             lambda d, c: self._write_aidl(d, c)),
            ("CPP",     RAGDSPyCppAgent,     lambda a: a.run(module_spec),
             lambda d, c: self._write_cpp(d, c)),
            ("SELinux", RAGDSPySELinuxAgent, lambda a: a.run(module_spec),
             lambda d, c: self._write_selinux(d, c)),
            ("Build",   RAGDSPyBuildAgent,   lambda a: a.run(module_spec),
             lambda d, c: self._write_build(d, c)),
        ]

        for name, AgentClass, runner, writer in sub_agents:
            t0 = time.time()
            try:
                agent   = AgentClass(**self._cfg_base)
                output  = runner(agent)
                elapsed = time.time() - t0
                results[name] = output or ""

                if output:
                    written = writer(domain, output)
                    paths   = ", ".join(str(p.relative_to(self._output_root))
                                        for p in written)
                    print(f"  [RAG+DSPy ARCHITECT] {name:<8} → ✓  wrote: {paths}  ({elapsed:.1f}s)")
                else:
                    print(f"  [RAG+DSPy ARCHITECT] {name:<8} → ⚠ empty — file not written ({elapsed:.1f}s)")

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