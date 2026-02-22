"""
dspy_opt/
═══════════════════════════════════════════════════════════════════
DSPy-based automatic prompt optimisation for AOSP HAL generation.

Replaces hand-crafted prompt variants (minimal/detailed/conservative/
aggressive from condition 2) with MIPROv2-optimised prompts that are
learned from measurable output quality signals.

Components:
  hal_signatures.py  — DSPy Signature definitions (task contracts)
  hal_modules.py     — DSPy Module wrappers (ChainOfThought per agent)
  metrics.py         — Evaluation metric functions (what MIPROv2 optimises)
  validators.py      — Compile/parse validators called by metrics.py
  optimizer.py       — MIPROv2 runner (run once, saves programs to saved/)

Workflow:
  1. Run condition 2 (multi_main_adaptive.py) to generate baseline output
  2. Run optimizer.py — reads labelled VSS signals as training examples,
     optimises each module's prompt, saves to dspy_opt/saved/
  3. Run condition 3 (multi_main_rag_dspy.py) — loads saved programs

Quick start:
    from dspy_opt import get_module, METRIC_REGISTRY, score_file
    module = get_module("aidl")                  # load optimised module
    result = module(domain=..., properties=..., aosp_context=...)
    score  = METRIC_REGISTRY["aidl"](None, result)
    score  = score_file("aidl", Path("IVehicle.aidl").read_text())
═══════════════════════════════════════════════════════════════════
"""

from dspy_opt.hal_modules import (
    AIDLModule, VHALCppModule, SELinuxModule, BuildFileModule,
    VINTFModule, DesignDocModule, PlantUMLModule, AndroidAppModule,
    AndroidLayoutModule, BackendAPIModule, BackendModelModule,
    SimulatorModule, get_module, MODULE_REGISTRY,
)
from dspy_opt.metrics     import METRIC_REGISTRY, score_file
from dspy_opt.validators  import validate, print_availability_report

__all__ = [
    # Modules
    "AIDLModule", "VHALCppModule", "SELinuxModule", "BuildFileModule",
    "VINTFModule", "DesignDocModule", "PlantUMLModule", "AndroidAppModule",
    "AndroidLayoutModule", "BackendAPIModule", "BackendModelModule",
    "SimulatorModule",
    # Helpers
    "get_module", "MODULE_REGISTRY", "METRIC_REGISTRY",
    # Compile-aware scoring
    "score_file", "validate", "print_availability_report",
]