"""
agents/rag_dspy_android_app_agent.py
═══════════════════════════════════════════════════════════════════
RAG+DSPy Android Automotive app generation agent (condition 3).

Generates Kotlin Fragments and XML layouts for each HAL module,
using real CarPropertyManager examples retrieved from ChromaDB
(aosp_car_api collection) and DSPy-optimised prompts.

Interface matches LLMAndroidAppAgentAdaptive.run():
    agent.run(module_signal_map, properties)
═══════════════════════════════════════════════════════════════════
"""

from __future__ import annotations

import time
from pathlib import Path
from agents.rag_dspy_mixin import RAGDSPyMixin


class RAGDSPyAndroidAppAgent(RAGDSPyMixin):
    """
    Generates Android Automotive Kotlin Fragments and XML layouts
    using RAG + DSPy.

    Produces the same output structure as LLMAndroidAppAgentAdaptive:
      android_app/src/main/java/com/vss/hal/<Domain>Fragment.kt
      android_app/src/main/res/layout/fragment_<domain>.xml
      android_app/src/main/AndroidManifest.xml  (if not present)

    Parameters
    ----------
    dspy_programs_dir : str  — root dir for saved DSPy programs
    rag_top_k         : int  — AOSP chunks to retrieve per call
    rag_db_path       : str  — ChromaDB path
    output_dir        : str  — root output directory
    """

    AGENT_TYPE        = "android_app"
    DSPY_OUTPUT_FIELD = "kotlin_code"

    _BASE_PACKAGE = "com.vss.hal"

    def __init__(
        self,
        dspy_programs_dir: str = "dspy_opt/saved",
        rag_top_k:         int = 3,
        rag_db_path:       str = "rag/chroma_db",
        output_dir:        str = "android_app",
    ):
        self._init_rag_dspy(
            dspy_programs_dir=dspy_programs_dir,
            rag_top_k=rag_top_k,
            rag_db_path=rag_db_path,
        )
        self._output_dir = Path(output_dir)

        # Layout module loaded separately (different signature + output field)
        self._layout_module = self._load_layout_module(dspy_programs_dir)

    def _load_layout_module(self, programs_dir: str):
        """Load the Android layout DSPy module independently."""
        try:
            from dspy_opt.hal_modules import get_module
            return get_module(
                "android_layout",
                programs_dir=programs_dir,
                auto_load=True,
            )
        except Exception as e:
            self._log(f"Layout module not available: {e}")
            return None

    def run(
        self,
        module_signal_map: dict,
        properties:        list,
    ) -> None:
        """
        Generate Kotlin Fragments and XML layouts for all HAL modules.

        Parameters
        ----------
        module_signal_map : dict
            {domain: [signal_names]} from plan_modules_from_spec()
        properties : list
            Full list of HAL property objects (all modules combined)
        """
        t_start = time.time()
        self._log(
            f"Generating Android app for {len(module_signal_map)} module(s)"
        )

        for domain, signal_names in module_signal_map.items():
            if not signal_names:
                self._log(f"Skipping {domain} — empty module")
                continue
            self._generate_module(domain, signal_names, properties)

        elapsed = time.time() - t_start
        self._log(f"Android app generation complete ({elapsed:.1f}s)")

    def _generate_module(
        self,
        domain:       str,
        signal_names: list[str],
        all_properties: list,
    ) -> None:
        """Generate Fragment + Layout for one HAL module."""
        # Resolve property objects for this module
        prop_ids = set(signal_names)
        module_props = [
            p for p in all_properties
            if getattr(p, "id", "") in prop_ids
        ]

        # Build property listing for prompt
        prop_lines = "\n".join(
            f"- {getattr(p, 'id', '')} ({getattr(p, 'type', 'BOOLEAN')}, "
            f"{getattr(p, 'access', 'READ')})"
            for p in module_props
        ) or "\n".join(f"- {name}" for name in signal_names[:10])

        # Targeted RAG query for CarPropertyManager Kotlin examples
        prop_type_sample = " ".join(
            getattr(p, "type", "") for p in module_props[:4]
        )
        queries = [
            f"CarPropertyManager getProperty {domain} Kotlin Fragment Android",
            f"registerCallback CarPropertyEventCallback {prop_type_sample}",
            f"Car.createCar getCarManager VEHICLE android automotive",
        ]
        aosp_context = self._retrieve_multi(queries)

        # ── Kotlin Fragment ─────────────────────────────────────
        kt_content = self._generate(
            domain       = domain,
            properties   = prop_lines,
            aosp_context = aosp_context,
        )
        self._write_kotlin(domain, kt_content)

        # ── XML Layout ──────────────────────────────────────────
        if self._layout_module is not None:
            layout_context = self._retrieve(
                f"Android layout XML {domain} property TextView Switch SeekBar"
            )
            try:
                result = self._layout_module(
                    domain       = domain,
                    properties   = prop_lines,
                    aosp_context = layout_context,
                )
                layout_content = getattr(result, "layout_xml", "") or ""
                self._write_layout(domain, layout_content)
            except Exception as e:
                self._log(f"Layout generation failed for {domain}: {e}")
        else:
            self._log(f"Skipping layout for {domain} — module not available")

    def _write_kotlin(self, domain: str, content: str) -> None:
        """Write Kotlin Fragment file."""
        class_name = f"{domain.capitalize()}Fragment"
        pkg_path   = self._BASE_PACKAGE.replace(".", "/")
        out_path   = (
            self._output_dir
            / "src/main/java"
            / pkg_path
            / f"{class_name}.kt"
        )
        out_path.parent.mkdir(parents=True, exist_ok=True)

        if content and content.strip():
            out_path.write_text(content, encoding="utf-8")
            self._log(f"Wrote {class_name}.kt ({len(content)} chars) ✓")
        else:
            self._log(f"WARNING: empty Kotlin output for {domain}")

    def _write_layout(self, domain: str, content: str) -> None:
        """Write Android XML layout file."""
        filename = f"fragment_{domain.lower()}.xml"
        out_path = self._output_dir / "src/main/res/layout" / filename
        out_path.parent.mkdir(parents=True, exist_ok=True)

        if content and content.strip():
            out_path.write_text(content, encoding="utf-8")
            self._log(f"Wrote {filename} ({len(content)} chars) ✓")
        else:
            self._log(f"WARNING: empty layout output for {domain}")