"""
agents/rag_dspy_backend_agent.py
═══════════════════════════════════════════════════════════════════
RAG+DSPy Python backend server generation agent (condition 3).

Generates the FastAPI server, Pydantic models, and HAL property
simulator for each module, using DSPy-optimised prompts and RAG
context retrieved from the aosp_docs collection.

Interface matches LLMBackendAgentAdaptive.run():
    agent.run(module_signal_map, properties)
═══════════════════════════════════════════════════════════════════
"""

from __future__ import annotations

import time
from pathlib import Path
from agents.rag_dspy_mixin import RAGDSPyMixin


class RAGDSPyBackendAgent(RAGDSPyMixin):
    """
    Generates FastAPI backend server, Pydantic models, and simulators
    for HAL properties using RAG + DSPy.

    Produces the same output structure as LLMBackendAgentAdaptive:
      output/backend/vss_dynamic_server/
        main.py
        models_<domain>.py
        simulator_<domain>.py
        requirements.txt
        config.py

    Run the generated server with:
        cd output/backend/vss_dynamic_server && uvicorn main:app --reload

    Parameters
    ----------
    dspy_programs_dir : str  — root dir for saved DSPy programs
    rag_top_k         : int  — AOSP chunks to retrieve per call
    rag_db_path       : str  — ChromaDB path
    output_dir        : str  — root output directory for backend files
    """

    AGENT_TYPE        = "backend"
    DSPY_OUTPUT_FIELD = "api_code"

    _SERVER_DIR = "output/backend/vss_dynamic_server"

    def __init__(
        self,
        dspy_programs_dir: str = "dspy_opt/saved",
        rag_top_k:         int = 3,
        rag_db_path:       str = "rag/chroma_db",
        output_dir:        str = "output/backend/vss_dynamic_server",
    ):
        self._init_rag_dspy(
            dspy_programs_dir=dspy_programs_dir,
            rag_top_k=rag_top_k,
            rag_db_path=rag_db_path,
        )
        self._output_dir = Path(output_dir)
        self._output_dir.mkdir(parents=True, exist_ok=True)

        # Load model and simulator modules separately
        self._model_module     = self._load_sub_module("backend_model", dspy_programs_dir)
        self._simulator_module = self._load_sub_module("simulator",     dspy_programs_dir)

    def _load_sub_module(self, agent_type: str, programs_dir: str):
        """Load a sub-module (model / simulator) independently."""
        try:
            from dspy_opt.hal_modules import get_module
            return get_module(agent_type, programs_dir=programs_dir, auto_load=True)
        except Exception as e:
            self._log(f"{agent_type} module not available: {e}")
            return None

    def run(
        self,
        module_signal_map: dict,
        properties:        list,
    ) -> None:
        """
        Generate backend server files for all HAL modules.

        Parameters
        ----------
        module_signal_map : dict
            {domain: [signal_names]} from plan_modules_from_spec()
        properties : list
            Full list of HAL property objects (all modules combined)
        """
        t_start = time.time()
        self._log(
            f"Generating backend for {len(module_signal_map)} module(s)"
        )

        # ── Per-module files (models + simulators) ──────────────
        for domain, signal_names in module_signal_map.items():
            if not signal_names:
                self._log(f"Skipping {domain} — empty module")
                continue
            self._generate_module_files(domain, signal_names, properties)

        # ── Shared files (main.py, config.py, requirements.txt) ─
        self._generate_shared_files(module_signal_map, properties)

        elapsed = time.time() - t_start
        self._log(
            f"Backend generation complete ({elapsed:.1f}s) "
            f"→ {self._output_dir}"
        )
        self._log(
            f"Run: cd {self._output_dir} && uvicorn main:app --reload"
        )

    def _generate_module_files(
        self,
        domain:         str,
        signal_names:   list[str],
        all_properties: list,
    ) -> None:
        """Generate models_<domain>.py and simulator_<domain>.py."""
        # Resolve property objects for this module
        prop_ids     = set(signal_names)
        module_props = [
            p for p in all_properties
            if getattr(p, "id", "") in prop_ids
        ]

        prop_lines = "\n".join(
            f"- {getattr(p, 'id', '')} "
            f"({getattr(p, 'type', 'BOOLEAN')}, "
            f"{getattr(p, 'access', 'READ')})"
            for p in module_props
        ) or "\n".join(f"- {n}" for n in signal_names[:10])

        # ── Pydantic models ─────────────────────────────────────
        if self._model_module is not None:
            model_context = self._retrieve(
                f"Pydantic BaseModel {domain} property type validation Python"
            )
            try:
                result       = self._model_module(
                    properties   = prop_lines,
                    aosp_context = model_context,
                )
                model_content = getattr(result, "models_code", "") or ""
                self._write(f"models_{domain.lower()}.py", model_content, "model")
            except Exception as e:
                self._log(f"Models generation failed for {domain}: {e}")
        else:
            self._log(f"Skipping models for {domain} — module not available")

        # ── Simulator ───────────────────────────────────────────
        if self._simulator_module is not None:
            # Build realistic value ranges per type for the prompt
            ranges = self._build_value_ranges(module_props)
            sim_context = self._retrieve(
                f"Python asyncio simulator {domain} property random vehicle"
            )
            try:
                result    = self._simulator_module(
                    domain          = domain,
                    properties      = f"{prop_lines}\n\nValue ranges:\n{ranges}",
                    aosp_context    = sim_context,
                )
                sim_content = getattr(result, "simulator_code", "") or ""
                self._write(f"simulator_{domain.lower()}.py", sim_content, "simulator")
            except Exception as e:
                self._log(f"Simulator generation failed for {domain}: {e}")
        else:
            self._log(f"Skipping simulator for {domain} — module not available")

    def _generate_shared_files(
        self,
        module_signal_map: dict,
        all_properties:    list,
    ) -> None:
        """Generate main.py, config.py, and requirements.txt."""
        domains = [d for d, sigs in module_signal_map.items() if sigs]
        domain  = domains[0] if domains else "VEHICLE"

        # Build combined property listing
        prop_lines = "\n".join(
            f"- {getattr(p, 'id', '')} "
            f"({getattr(p, 'type', 'BOOLEAN')}, "
            f"{getattr(p, 'access', 'READ')})"
            for p in all_properties[:20]   # cap for prompt size
        )

        # ── main.py (FastAPI app) ───────────────────────────────
        api_context = self._retrieve_multi([
            f"FastAPI REST API async endpoint automotive vehicle property",
            f"WebSocket real-time property stream FastAPI Python",
            f"CORS middleware uvicorn FastAPI app HAL bridge",
        ])
        main_content = self._generate(
            domain       = domain,
            properties   = prop_lines,
            aosp_context = api_context,
        )
        self._write("main.py", main_content, "api")

        # ── config.py (static, no DSPy needed) ─────────────────
        config_content = self._generate_config(domains)
        self._write("config.py", config_content, "config")

        # ── requirements.txt (static) ───────────────────────────
        reqs = (
            "fastapi>=0.110.0\n"
            "uvicorn[standard]>=0.29.0\n"
            "pydantic>=2.0.0\n"
            "websockets>=12.0\n"
            "python-dotenv>=1.0.0\n"
        )
        self._write("requirements.txt", reqs, "requirements")

    def _build_value_ranges(self, props: list) -> str:
        """Build realistic value range hints for the simulator prompt."""
        type_ranges = {
            "BOOLEAN": "True/False",
            "FLOAT":   "0.0 – 100.0 (domain-specific)",
            "INT":     "0 – 255 (domain-specific)",
            "INT32":   "0 – 65535",
            "INT64":   "0 – 2^31",
            "STRING":  "short alphanumeric string",
        }
        lines = []
        for p in props[:10]:
            t     = getattr(p, "type", "BOOLEAN")
            name  = getattr(p, "id",   "UNKNOWN").split("_")[-1]
            lines.append(f"  {name}: {type_ranges.get(t, '0–100')}")
        return "\n".join(lines)

    def _generate_config(self, domains: list[str]) -> str:
        """Generate a static config.py for the backend server."""
        domain_list = ", ".join(f'"{d}"' for d in domains)
        return (
            '"""Backend server configuration."""\n\n'
            "HOST            = \"0.0.0.0\"\n"
            "PORT            = 8000\n"
            "LOG_LEVEL       = \"info\"\n"
            f"HAL_DOMAINS     = [{domain_list}]\n"
            "SIMULATOR_INTERVAL_S = 1.0\n"
            "MAX_WS_CLIENTS  = 10\n"
        )

    def _write(self, filename: str, content: str, label: str) -> None:
        """Write a generated file; log the outcome."""
        out_path = self._output_dir / filename
        if content and content.strip():
            out_path.write_text(content, encoding="utf-8")
            self._log(f"Wrote {filename} ({len(content)} chars) ✓")
        else:
            self._log(f"WARNING: empty {label} output — {filename} not written")