"""
agents/rag_dspy_design_doc_agent.py
═══════════════════════════════════════════════════════════════════
RAG+DSPy design document generation agent (condition 3).

Generates both the Markdown design document and PlantUML diagrams
using RAG-retrieved AOSP documentation examples and DSPy-optimised
prompts.

Interface matches DesignDocAgentAdaptive.run():
    agent.run(module_signal_map, properties, yaml_spec)
═══════════════════════════════════════════════════════════════════
"""

from __future__ import annotations

import time
from pathlib import Path
from agents.rag_dspy_mixin import RAGDSPyMixin


class RAGDSPyDesignDocAgent(RAGDSPyMixin):
    """
    Generates HAL design documents and PlantUML diagrams using RAG + DSPy.

    Produces the same output files as DesignDocAgentAdaptive:
      docs/design/DESIGN_DOCUMENT.md
      docs/design/architecture.puml
      docs/design/class_diagram.puml
      docs/design/sequence_diagram.puml
      docs/design/component_diagram.puml

    Parameters
    ----------
    dspy_programs_dir : str  — root dir for saved DSPy programs
    rag_top_k         : int  — AOSP chunks to retrieve per call
    rag_db_path       : str  — ChromaDB path
    output_dir        : str  — where to write generated files
    """

    AGENT_TYPE        = "design_doc"
    DSPY_OUTPUT_FIELD = "design_doc"

    _OUTPUT_DIR = Path("docs/design")

    # PlantUML diagram types to generate — each gets its own DSPy call
    _DIAGRAM_TYPES = [
        "architecture",
        "class_diagram",
        "sequence_diagram",
        "component_diagram",
    ]

    def __init__(
        self,
        dspy_programs_dir: str = "dspy_opt/saved",
        rag_top_k:         int = 3,
        rag_db_path:       str = "rag/chroma_db",
        output_dir:        str = "docs/design",
        output_root:       str = "",
    ):
        self._init_rag_dspy(
            dspy_programs_dir=dspy_programs_dir,
            rag_top_k=rag_top_k,
            rag_db_path=rag_db_path,
        )
        # If output_root is provided (C3), place docs under it.
        # Otherwise use output_dir as-is (C1/C2 behaviour).
        if output_root:
            self._output_dir = Path(output_root) / "docs" / "design"
        else:
            self._output_dir = Path(output_dir)
        self._output_dir.mkdir(parents=True, exist_ok=True)

        # PlantUML module loaded separately (different signature)
        self._puml_module = self._load_puml_module(dspy_programs_dir)

    def _load_puml_module(self, programs_dir: str):
        """Load the PlantUML DSPy module independently."""
        try:
            from dspy_opt.hal_modules import get_module
            return get_module(
                "puml",
                programs_dir=programs_dir,
                auto_load=True,
            )
        except Exception as e:
            self._log(f"PlantUML module not available: {e}")
            return None

    def run(
        self,
        module_signal_map: dict,
        properties:        list,
        yaml_spec:         str,
    ) -> None:
        """
        Generate design documents and PlantUML diagrams.

        Parameters
        ----------
        module_signal_map : dict
            {domain: [signal_names]} from plan_modules_from_spec()
        properties : list
            Full list of HAL property objects from full_spec.properties
        yaml_spec : str
            Raw YAML spec string — used as additional context
        """
        t_start  = time.time()
        domains  = list(module_signal_map.keys())
        domain   = domains[0] if domains else "VEHICLE"

        # Build modules summary for the prompt
        modules_summary = "\n".join(
            f"{d}: {len(sigs)} signals"
            for d, sigs in module_signal_map.items()
            if sigs
        )

        self._log(f"Generating design docs for domains: {domains}")

        # ── 1. Design document ──────────────────────────────────
        doc_context = self._retrieve(
            f"{domain} HAL AOSP design document architecture overview README"
        )
        doc_content = self._generate(
            domain       = domain,
            modules      = modules_summary,
            aosp_context = doc_context,
        )
        self._write("DESIGN_DOCUMENT.md", doc_content, "design_doc")

        # ── 2. PlantUML diagrams ────────────────────────────────
        if self._puml_module is not None:
            components = (
                f"VSS signals → {domain}HALService → CarService → "
                f"AndroidApp\nModules: {', '.join(domains)}"
            )
            puml_context = self._retrieve(
                f"AOSP architecture PlantUML component diagram HAL"
            )
            for diagram_type in self._DIAGRAM_TYPES:
                self._generate_diagram(diagram_type, domain, components, puml_context)
        else:
            self._log("Skipping PlantUML — module not available")

        elapsed = time.time() - t_start
        self._log(f"Design docs complete ({elapsed:.1f}s) → {self._output_dir}")

    def _generate_diagram(
        self,
        diagram_type: str,
        domain:       str,
        components:   str,
        puml_context: str,
    ) -> None:
        """Generate one PlantUML diagram file."""
        try:
            result = self._puml_module(
                domain       = f"{domain} — {diagram_type.replace('_', ' ').title()}",
                components   = components,
                aosp_context = puml_context,
            )
            puml_content = getattr(result, "puml", "") or ""
            self._write(f"{diagram_type}.puml", puml_content, "puml")
        except Exception as e:
            self._log(f"PlantUML {diagram_type} failed: {e}")

    def _write(self, filename: str, content: str, agent_type: str) -> None:
        """Write generated content to file; log outcome."""
        path = self._output_dir / filename
        if content and content.strip():
            path.write_text(content, encoding="utf-8")
            self._log(f"Wrote {filename} ({len(content)} chars) ✓")
        else:
            self._log(f"WARNING: empty output for {filename} — file not written")