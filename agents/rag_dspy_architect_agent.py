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
import re

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
        dspy_programs_dir:  str  = "dspy_opt/saved",
        rag_top_k:          int  = 3,
        rag_db_path:        str  = "rag/chroma_db",
        output_root:        str  = "output_rag_dspy",
        enable_chunk_retry: bool = False,
    ):
        # Config for HAL agents — they only take these 3 params
        self._cfg_base = dict(
            dspy_programs_dir=dspy_programs_dir,
            rag_top_k=rag_top_k,
            rag_db_path=rag_db_path,
        )
        self._output_root       = Path(output_root)
        self._enable_chunk_retry = enable_chunk_retry
        self.last_chunk_retries = 0  # set by _write_cpp(); read by callers for the module summary line

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

    def _write_cpp(self, domain: str, content) -> list[Path]:
        """Write C++ files. Accepts either a string (impl only) or a dict
        (header, impl, main_service, android_bp) from the modern agent."""
        domain_cap  = domain.capitalize()
        domain_lower = domain.lower()
        written = []

        # Surfaced separately (not via return value, to avoid changing this
        # method's existing return-type contract for other callers) so the
        # module-level summary line can report chunk-retry activity
        # alongside module-level (C4 feedback loop) retries — previously
        # chunk retries only printed to console mid-generation and were
        # never reflected in the final "[C4 MODULE ...] retries=N" line,
        # which reports a DIFFERENT retry mechanism entirely and made
        # "retries=0" read as if nothing needed fixing when chunk-level
        # retries had, in fact, already silently fixed missing cases.
        self.last_chunk_retries = content.get("chunk_retries", 0) if isinstance(content, dict) else 0

        if isinstance(content, dict):
            # Modern multi-file output from RagDspyCppAgent.generate()
            # Write all 4 files under impl/ using VehicleHalService{Domain} naming
            # — consistent with legacy path and apply_aosp14_fixes.sh
            if content.get("header"):
                written.append(self._write(
                    f"{self._CPP_DIR}/VehicleHalService{domain_cap}.h",
                    content["header"]))
            if content.get("impl"):
                written.append(self._write(
                    f"{self._CPP_DIR}/VehicleHalService{domain_cap}.cpp",
                    content["impl"]))
            if content.get("main_service"):
                written.append(self._write(
                    f"{self._CPP_DIR}/VehicleService{domain_cap}.cpp",
                    content["main_service"]))
            if content.get("android_bp"):
                # Same reasoning as _write_build: NEVER name this
                # "Android.bp" — Soong would pick it up and it would
                # collide/overwrite across domains. Kept as a
                # debug/audit artifact only; VssGlueAgent's
                # aidl/impl/vss/Android.bp is the real build file.
                written.append(self._write(
                    f"{self._CPP_DIR}/Android_{domain_lower}.bp.draft",
                    content["android_bp"]))
        else:
            # Legacy string output — write single impl file
            fname = f"VehicleHalService{domain_cap}.cpp"
            written.append(self._write(f"{self._CPP_DIR}/{fname}", content))

        return written

    def _clean_selinux(self, content: str) -> str:
        """Strip everything before the first valid SELinux statement.

        Anchors on the first line starting with a known SELinux keyword
        (type, allow, require, etc.) and discards everything before it —
        markdown fences, bare braces, JSON wrappers, comments.
        Lone { } lines are removed from the body.
        Inline leading { on a line is stripped.
        """
        if not content or not isinstance(content, str):
            return content or ""

        # Strip markdown code fences
        content = re.sub(r'```[a-zA-Z]*', '', content)

        SEL_KW = (
            "type ", "allow ", "neverallow ", "dontaudit ", "auditallow ",
            "require", "typeattribute ", "attribute ", "permissive ",
            "typetransition ", "typechange ", "typemember ",
        )

        lines = content.splitlines()

        # Find first line starting with a real SELinux keyword
        start = 0
        for i, line in enumerate(lines):
            stripped = line.strip()
            if any(stripped.startswith(kw) for kw in SEL_KW):
                start = i
                break

        # Keep from that line onward; drop lone { } and backtick lines
        body = []
        for line in lines[start:]:
            stripped = line.strip()
            if stripped in {"", "{", "}", "```"}:
                continue
            # Strip inline leading { e.g. "{ type foo domain;"
            if stripped.startswith("{") and not stripped.startswith("{%"):
                line = line.lstrip().lstrip("{").lstrip()
            body.append(line)

        return "\n".join(body).strip()

    def _write_selinux(self, domain: str, content: str) -> list[Path]:
        """Write SELinux .te policy file."""
        content = self._clean_selinux(content)
        domain_lower = domain.lower()
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
        """Write the per-module Android.bp DRAFT from RAGDSPyBuildAgent.

        IMPORTANT: this is intentionally NOT named "Android.bp".

        Soong only ever reads a file literally named `Android.bp` in a
        given directory — so if this method wrote `impl/Android.bp`,
        every domain run (AIDL/CPP/SELinux/Build loop happens once per
        domain, sequentially) would silently overwrite the previous
        domain's build file. With srcs:["*.cpp"] (the pattern shown in
        BuildFileSignature's own example), whichever domain ran LAST
        would end up owning a Soong module that tries to compile EVERY
        domain's .cpp — including all 7 domains' VehicleService{Domain}.cpp
        main() entrypoints in one binary, which fails to link
        ("multiple definition of 'main'").

        The actual, authoritative Android.bp for this whole HAL tree is
        written later by VssGlueAgent (aidl/impl/vss/Android.bp), which
        explicitly lists each domain's .cpp by name via
        ../../../impl/VehicleHalService{Domain}.cpp — no wildcards, no
        cross-domain collision. So RAGDSPyBuildAgent's output here is
        kept only as a per-domain debug/audit artifact under a name
        Soong will never pick up.
        """
        domain_lower = domain.lower()
        return [self._write(
            f"{self._BUILD_DIR}/Android_{domain_lower}.bp.draft",
            content,
        )]

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

        # Read generated AIDL enum to inject exact prop names into CPP agent.
        # All custom VSS properties are merged into the single aidl_property/
        # VehicleProperty.aidl on the build system — per-domain headers like
        # VehiclePropertyAdas.h no longer exist. The LLM must use:
        #   VehicleProperty::PROP_NAME  (unified enum, NOT VehiclePropertyAdas::)
        # We still read the per-domain .aidl file to get the exact property
        # names, but we rewrite the enum declaration to say VehicleProperty
        # so the LLM learns the correct prefix to use in static_cast<>.
        def _get_aidl_content(domain: str) -> str:
            import glob as _glob
            aidl_dir = str(self._output_root / "hardware/interfaces/automotive/vehicle/aidl/android/hardware/automotive/vehicle")
            files = _glob.glob(f"{aidl_dir}/VehicleProperty{domain.capitalize()}.aidl")
            if not files:
                return ""
            raw = open(files[0], errors="ignore").read()
            # Rewrite "enum VehiclePropertyAdas {" → "enum VehicleProperty {"
            # so the LLM sees the correct enum name that exists in VehicleProperty.h
            # after the aidl_property merge. The property constant names are identical.
            import re as _re
            raw = _re.sub(
                r"\benum\s+VehicleProperty\w+\s*\{",
                "enum VehicleProperty {",
                raw,
            )
            return (
                "\n=== Generated AIDL enum (use these exact prop IDs) ===\n"
                "// NOTE: All VSS properties are merged into VehicleProperty in VehicleProperty.h\n"
                "// Use VehicleProperty::PROP_NAME — NOT VehiclePropertyAdas:: or other per-domain prefixes\n"
                + raw
            )

        # Lightweight spec wrapper that injects the generated AIDL enum
        # content into to_llm_spec() so a.run() → generate_chunked()
        # has real property names for both chunking and hallucination
        # cross-check — identical to gen_hal_minimal_c4.py's _PatchedSpec.
        class _AIDLPatchedSpec:
            def __init__(self, spec, aidl_suffix: str):
                self._spec   = spec
                self._suffix = aidl_suffix
            def __getattr__(self, name):
                return getattr(self._spec, name)
            def to_llm_spec(self) -> str:
                return self._spec.to_llm_spec() + self._suffix

        patched_spec = _AIDLPatchedSpec(
            module_spec,
            _get_aidl_content(module_spec.domain),
        )

        sub_agents = [
            ("AIDL",    RAGDSPyAIDLAgent,    lambda a: a.run(module_spec),
             lambda d, c: self._write_aidl(d, c)),
            ("CPP",     RAGDSPyCppAgent,
             # C4/C4-minimal: use a.run() → generate_chunked() with retry
             # C3: use a.inner.generate() — single-shot, no chunking,
             #     preserving original C3 design (chunking is a C4 contribution)
             (lambda a: (
                 print(f"  [ARCHITECT DEBUG] aidl_dir_computed={str(self._output_root / self._AIDL_DIR)!r} "
                       f"enable_chunk_retry={self._enable_chunk_retry}"),
                 a.run(patched_spec, aidl_dir=str(self._output_root / self._AIDL_DIR))
             )[1])
             if self._enable_chunk_retry else
             (lambda a: a.inner.generate(
                 domain     = module_spec.domain,
                 properties = module_spec.to_llm_spec() + _get_aidl_content(module_spec.domain),
             )),
             lambda d, c: self._write_cpp(d, c)),
            ("SELinux", RAGDSPySELinuxAgent, lambda a: a.run(module_spec),
             lambda d, c: self._write_selinux(d, c)),
            ("Build",   RAGDSPyBuildAgent,   lambda a: a.run(module_spec),
             lambda d, c: self._write_build(d, c)),
        ]

        for name, AgentClass, runner, writer in sub_agents:
            t0 = time.time()
            try:
                if AgentClass is RAGDSPyCppAgent:
                    agent = AgentClass(
                        enable_chunk_retry=self._enable_chunk_retry,
                        **self._cfg_base,
                    )
                else:
                    agent = AgentClass(**self._cfg_base)
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