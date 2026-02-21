"""
dspy_opt/hal_modules.py
═══════════════════════════════════════════════════════════════════
DSPy Module wrappers — one ChainOfThought module per generation agent.

Each module:
  1. Wraps a Signature from hal_signatures.py with ChainOfThought
  2. Can be compiled (optimised) by MIPROv2 in optimizer.py
  3. Can be saved to disk and reloaded by RAG+DSPy agents at runtime

Factory pattern is used so every module follows the identical
structure — only the Signature and output field name differ.

Saving / loading optimised programs:
    module = AIDLModule()
    module.save("dspy_opt/saved/aidl_program")   # after optimisation
    module.load("dspy_opt/saved/aidl_program")   # before inference

Quick usage (unoptimised, for testing):
    import dspy
    lm = dspy.LM("ollama/qwen2.5-coder:32b", api_base="http://localhost:11434")
    dspy.configure(lm=lm)

    module = AIDLModule()
    result = module(
        domain="ADAS",
        properties="- Name: ABS_IsEnabled\n  Type: BOOLEAN\n  Access: READ_WRITE",
        aosp_context="",  # empty = no RAG context
    )
    print(result.aidl_code)
═══════════════════════════════════════════════════════════════════
"""

from __future__ import annotations

import logging
from pathlib import Path
from typing import Optional

import dspy

from dspy_opt.hal_signatures import (
    AIDLSignature, VHALCppSignature, SELinuxSignature, BuildFileSignature,
    VINTFSignature, DesignDocSignature, PlantUMLSignature,
    AndroidAppSignature, AndroidLayoutSignature,
    BackendAPISignature, BackendModelSignature, SimulatorSignature,
    SIGNATURE_REGISTRY,
)

logger = logging.getLogger(__name__)


# ─────────────────────────────────────────────────────────────────
# Base class shared by all modules
# ─────────────────────────────────────────────────────────────────

class _BaseHALModule(dspy.Module):
    """
    Base DSPy module for HAL generation.

    Subclasses set:
        SIGNATURE_CLASS   — the dspy.Signature subclass
        OUTPUT_FIELD      — name of the output field on the prediction

    Provides:
        forward(**kwargs) → dspy.Prediction
        generate_text(**kwargs) → str   (convenience wrapper)
        save(path) / load(path)         (thin wrappers for persistence)
        is_optimised                    (True after load() succeeds)
    """
    SIGNATURE_CLASS = None   # set by subclass
    OUTPUT_FIELD:   str = "" # set by subclass

    def __init__(self):
        super().__init__()
        if self.SIGNATURE_CLASS is None:
            raise NotImplementedError("Subclass must set SIGNATURE_CLASS")
        self.generate = dspy.ChainOfThought(self.SIGNATURE_CLASS)
        self._optimised = False

    # ── Core DSPy forward ────────────────────────────────────────
    def forward(self, **kwargs) -> dspy.Prediction:
        """
        Run the module. Accepts all InputFields defined in SIGNATURE_CLASS.
        Returns a dspy.Prediction; access output via result.<OUTPUT_FIELD>.
        """
        return self.generate(**kwargs)

    # ── Convenience wrapper ──────────────────────────────────────
    def generate_text(self, **kwargs) -> str:
        """
        Run forward() and return the output field as a plain string.
        Returns "" on failure rather than raising.
        """
        try:
            result = self.forward(**kwargs)
            return getattr(result, self.OUTPUT_FIELD, "") or ""
        except Exception as e:
            logger.error(
                f"[{self.__class__.__name__}] Generation failed: {e}"
            )
            return ""

    # ── Persistence ──────────────────────────────────────────────
    def save(self, path: str | Path) -> None:
        """
        Save the optimised program state to disk.
        Call this after dspy.MIPROv2.compile() succeeds.
        """
        path = Path(path)
        path.mkdir(parents=True, exist_ok=True)
        state_path = path / "program.json"
        try:
            # dspy.Module.dump_state() returns a JSON-serialisable dict
            import json
            state = self.dump_state()
            state_path.write_text(
                json.dumps(state, indent=2, ensure_ascii=False),
                encoding="utf-8",
            )
            logger.info(f"[{self.__class__.__name__}] Saved → {state_path}")
        except Exception as e:
            logger.error(f"[{self.__class__.__name__}] Save failed: {e}")

    def load(self, path: str | Path) -> bool:
        """
        Load a previously saved optimised program from disk.
        Returns True on success, False if not found or invalid.
        """
        path = Path(path)
        state_path = path / "program.json"
        if not state_path.exists():
            logger.debug(
                f"[{self.__class__.__name__}] No saved program at {state_path}"
            )
            return False
        try:
            import json
            state = json.loads(state_path.read_text(encoding="utf-8"))
            self.load_state(state)
            self._optimised = True
            logger.info(
                f"[{self.__class__.__name__}] Loaded optimised program ← {state_path}"
            )
            return True
        except Exception as e:
            logger.warning(
                f"[{self.__class__.__name__}] Load failed ({state_path}): {e}"
            )
            return False

    @property
    def is_optimised(self) -> bool:
        """True if an optimised program has been loaded from disk."""
        return self._optimised


# ─────────────────────────────────────────────────────────────────
# Module factory — avoids repeating the same boilerplate 12 times
# ─────────────────────────────────────────────────────────────────

def _make_module_class(
    sig_class,
    output_field: str,
    class_name: str,
    docstring: str = "",
) -> type:
    """
    Dynamically create a _BaseHALModule subclass for a given Signature.

    Parameters
    ----------
    sig_class    : dspy.Signature subclass
    output_field : name of the output field on the Prediction
    class_name   : class name for the generated type (e.g. "AIDLModule")
    docstring    : optional docstring for the generated class

    Returns
    -------
    type — a fully usable dspy.Module subclass
    """
    return type(
        class_name,
        (_BaseHALModule,),
        {
            "SIGNATURE_CLASS": sig_class,
            "OUTPUT_FIELD":    output_field,
            "__doc__":         docstring or f"DSPy module for {class_name}",
        },
    )


# ─────────────────────────────────────────────────────────────────
# A. HAL LAYER MODULES  (5)
# ─────────────────────────────────────────────────────────────────

AIDLModule = _make_module_class(
    AIDLSignature, "aidl_code", "AIDLModule",
    "Generates AOSP VHAL AIDL interface definitions."
)

VHALCppModule = _make_module_class(
    VHALCppSignature, "cpp_code", "VHALCppModule",
    "Generates VHAL C++ service implementation files."
)

SELinuxModule = _make_module_class(
    SELinuxSignature, "policy", "SELinuxModule",
    "Generates SELinux .te policy files for VHAL services."
)

BuildFileModule = _make_module_class(
    BuildFileSignature, "build_file", "BuildFileModule",
    "Generates Android.bp build files for VHAL modules."
)

VINTFModule = _make_module_class(
    VINTFSignature, "manifest", "VINTFModule",
    "Generates VINTF manifest XML and init.rc service definitions."
)

# ─────────────────────────────────────────────────────────────────
# B. DESIGN LAYER MODULES  (2)
# ─────────────────────────────────────────────────────────────────

DesignDocModule = _make_module_class(
    DesignDocSignature, "design_doc", "DesignDocModule",
    "Generates Markdown technical design documents for HAL modules."
)

PlantUMLModule = _make_module_class(
    PlantUMLSignature, "puml", "PlantUMLModule",
    "Generates PlantUML architecture diagram source files."
)

# ─────────────────────────────────────────────────────────────────
# C. ANDROID APP LAYER MODULES  (2)
# ─────────────────────────────────────────────────────────────────

AndroidAppModule = _make_module_class(
    AndroidAppSignature, "kotlin_code", "AndroidAppModule",
    "Generates Android Automotive Kotlin Fragment implementations."
)

AndroidLayoutModule = _make_module_class(
    AndroidLayoutSignature, "layout_xml", "AndroidLayoutModule",
    "Generates Android XML layout files for HAL property displays."
)

# ─────────────────────────────────────────────────────────────────
# D. BACKEND LAYER MODULES  (3)
# ─────────────────────────────────────────────────────────────────

BackendAPIModule = _make_module_class(
    BackendAPISignature, "api_code", "BackendAPIModule",
    "Generates FastAPI REST server implementations for HAL properties."
)

BackendModelModule = _make_module_class(
    BackendModelSignature, "models_code", "BackendModelModule",
    "Generates Pydantic data model definitions for HAL properties."
)

SimulatorModule = _make_module_class(
    SimulatorSignature, "simulator_code", "SimulatorModule",
    "Generates Python HAL property simulators for testing."
)


# ─────────────────────────────────────────────────────────────────
# Module registry
# Maps agent_type key → (ModuleClass, output_field)
# Used by optimizer.py and RAG+DSPy agents
# ─────────────────────────────────────────────────────────────────

MODULE_REGISTRY: dict[str, tuple[type, str]] = {
    # HAL layer
    "aidl":           (AIDLModule,         "aidl_code"),
    "cpp":            (VHALCppModule,       "cpp_code"),
    "selinux":        (SELinuxModule,       "policy"),
    "build":          (BuildFileModule,     "build_file"),
    "vintf":          (VINTFModule,         "manifest"),
    # Design layer
    "design_doc":     (DesignDocModule,     "design_doc"),
    "puml":           (PlantUMLModule,      "puml"),
    # App layer
    "android_app":    (AndroidAppModule,    "kotlin_code"),
    "android_layout": (AndroidLayoutModule, "layout_xml"),
    # Backend layer
    "backend":        (BackendAPIModule,    "api_code"),
    "backend_model":  (BackendModelModule,  "models_code"),
    "simulator":      (SimulatorModule,     "simulator_code"),
}


def get_module(
    agent_type: str,
    programs_dir: str | Path = "dspy_opt/saved",
    auto_load: bool = True,
) -> _BaseHALModule:
    """
    Instantiate a module for the given agent_type and optionally load
    a previously saved optimised program from disk.

    Parameters
    ----------
    agent_type   : key from MODULE_REGISTRY
    programs_dir : root directory containing saved program subdirs
    auto_load    : if True, attempt to load saved program automatically

    Returns
    -------
    _BaseHALModule instance (optimised if saved program found, else raw)

    Example
    -------
        module = get_module("aidl")
        result = module(domain="ADAS", properties="...", aosp_context="")
        print(result.aidl_code)
    """
    if agent_type not in MODULE_REGISTRY:
        raise ValueError(
            f"Unknown agent_type: '{agent_type}'. "
            f"Valid types: {sorted(MODULE_REGISTRY.keys())}"
        )

    module_class, _ = MODULE_REGISTRY[agent_type]
    module = module_class()

    if auto_load:
        save_path = Path(programs_dir) / f"{agent_type}_program"
        loaded = module.load(save_path)
        if loaded:
            print(f"[DSPy] {agent_type}: loaded optimised program ✓")
        else:
            print(f"[DSPy] {agent_type}: using unoptimised module "
                  f"(run optimizer.py to generate saved program)")

    return module


def list_optimised(programs_dir: str | Path = "dspy_opt/saved") -> dict[str, bool]:
    """
    Return a dict of {agent_type: is_saved} showing which modules
    have optimised programs saved to disk.

    Useful for pre-flight checks in multi_main_rag_dspy.py.
    """
    programs_dir = Path(programs_dir)
    return {
        agent_type: (programs_dir / f"{agent_type}_program" / "program.json").exists()
        for agent_type in MODULE_REGISTRY
    }