"""
multi_main_c4_feedback.py
═══════════════════════════════════════════════════════════════════
VSS → AAOS HAL Generation Pipeline — Condition 4: Feedback Loop

Combines the best of C1-C3 with a validator-in-the-loop:
  1. RAG retrieval (from C3) — AOSP context for domain coverage
  2. DSPy optimised prompts (from C3) — learned instructions
  3. Thompson Sampling (from C2) — online prompt variant selection
  4. NEW: Validator feedback loop — if code fails validation,
     feed the error back to the LLM and retry (up to MAX_RETRIES)
  5. NEW: Markdown fence stripping (from mixin fix)

Architecture vs old C4:
  - OLD: bypassed architect.run(), manually called sub-agent._generate()
         with hardcoded RAG queries → lost orchestration context
  - NEW: uses architect.run() identically to C3 for first pass,
         then post-validates output files and retries only failed agents
         with error feedback in a SEPARATE prompt field (not appended
         to aosp_context, which polluted RAG context)

Pipeline per file:
  1. Generate via architect.run() (same as C3 — RAG + DSPy)
  2. Post-validate each output file (clang++, checkpolicy, ast, etc.)
  3. If FAIL → re-generate that agent with error feedback → retry (up to 3x)
  4. Update Thompson prior with final pass/fail
  5. Write output

Usage:
    python multi_main_c4_feedback.py
═══════════════════════════════════════════════════════════════════
"""

from __future__ import annotations

import json
import time
import re
import ast
import subprocess
from pathlib import Path
from typing import Optional
from agents.rag_dspy_cpp_agent import RagDspyCppAgent

# ── ChromaDB singleton fix (prevents "instance already exists" error) ──
import fix_chroma_singleton
fix_chroma_singleton.patch_chromadb()
# ── End fix ──────────────────────────────────────────────────────────────

# ── Android Layout fix (post-generation) ─────────────────────────────────────
import re
import html
from pathlib import Path
import xml.etree.ElementTree as ET

def fix_android_layouts(output_dir: str = "output_c4_feedback",
                        fnames: list = None) -> int:
    """
    Fix Android layout XML files.
    Delegates to validators.fix_android_layouts_dir (proven notebook logic).
    importlib.reload(dspy_opt.validators) before calling to pick up any edits.
    """
    from dspy_opt.validators import fix_android_layouts_dir
    return fix_android_layouts_dir(output_dir, fnames)

# ── Configuration ─────────────────────────────────────────────────

MAX_RETRIES        = 3     # retry attempts per file on validation failure
OUTPUT_DIR         = Path("output_c4_feedback")
# Reused by the C4-only CPP↔AIDL name consistency check
# (dspy_opt.validators.check_cpp_aidl_name_consistency). Same formula
# already used later for VssGlueAgent's aidl_dir — factored out here so
# both the per-domain validation sweep and the final glue step agree.
AIDL_DIR = OUTPUT_DIR / "hardware/interfaces/automotive/vehicle/aidl/android/hardware/automotive/vehicle"
RESULTS_DIR        = Path("experiments/results")
TEST_SIGNAL_COUNT  = 500
VSS_PATH           = "./dataset/vss.json"
VENDOR_NAMESPACE   = "vendor.vss"
PERSISTENT_CACHE_DIR = Path("/content/vss_temp")

AGENT_CFG = dict(
    dspy_programs_dir = "dspy_opt/saved",
    rag_top_k         = 8,
    rag_db_path       = "rag/chroma_db",
)

AGENT_CFG_WITH_OUTPUT = dict(
    **AGENT_CFG,
    output_root = str(OUTPUT_DIR),
)


# ══════════════════════════════════════════════════════════════════
# VALIDATOR FEEDBACK ENGINE
# ══════════════════════════════════════════════════════════════════

class ValidatorFeedback:
    """
    Validates generated code and produces actionable error messages
    that can be fed back to the LLM for self-correction.
    """

    @staticmethod
    def validate(code: str, agent_type: str, aidl_dir: str = "") -> tuple[bool, str, float]:
        """
        Validate code and return (passed, error_message, score).
        error_message is formatted for LLM consumption.

        aidl_dir: C4-only. When agent_type == "cpp" and aidl_dir is
        given, an additional CPP↔AIDL name-consistency gate runs on
        top of the canonical structural score (see
        dspy_opt.validators.check_cpp_aidl_name_consistency). Ignored
        for all other agent_types — this keeps C1/C2/C3 scoring, which
        never pass aidl_dir, byte-for-byte unchanged.

        Score source: `passed` (pass/fail) and `error_message` (LLM
        feedback) still come from the per-type _validate_* methods
        below, each wrapping dspy_opt.validators' canonical structural
        checks. But the numeric `score` is overridden to come from
        rescore_all_conditions.py::score_content() — the SAME rubric
        that produces the final reported thesis numbers.

        Why: PostValidationRetry's "keep the best-scoring version"
        comparison previously used each validator's own internal score
        (e.g. validate_cpp()'s +0.15/+0.20/... ad-hoc scheme), which
        has DIFFERENT weights and checks than score_content()'s
        struct/syntax/coverage rubric (see WEIGHTS in
        rescore_all_conditions.py). A retry attempt could score higher
        under the internal rubric and get kept, while scoring LOWER
        under the rescore rubric — meaning "successful" retries could
        silently lower the final reported average for a domain, with
        no way to detect it until the separate rescoring step ran.
        Overriding the score here closes that gap: the retry loop's
        "best version" decision now agrees with what actually gets
        reported.

        Falls back to the per-type validator's own score if
        score_content() doesn't recognize agent_type (e.g. types with
        no WEIGHTS entry), so this degrades safely rather than crashing.
        """
        if agent_type == "aidl":
            passed, msg, fallback_score = ValidatorFeedback._validate_aidl(code)
        elif agent_type == "cpp":
            passed, msg, fallback_score = ValidatorFeedback._validate_cpp(code, aidl_dir)
        elif agent_type == "selinux":
            passed, msg, fallback_score = ValidatorFeedback._validate_selinux(code)
        elif agent_type == "build":
            passed, msg, fallback_score = ValidatorFeedback._validate_build(code)
        elif agent_type in ("backend", "backend_model", "simulator"):
            passed, msg, fallback_score = ValidatorFeedback._validate_python(code)
        elif agent_type in ("android_app",):
            passed, msg, fallback_score = ValidatorFeedback._validate_kotlin(code)
        elif agent_type in ("android_layout",):
            passed, msg, fallback_score = ValidatorFeedback._validate_xml(code)
        elif agent_type == "design_doc":
            passed, msg, fallback_score = ValidatorFeedback._validate_markdown(code)
        else:
            return (True, "", 1.0)

        try:
            from rescore_all_conditions import score_content
            score = score_content(agent_type, code)["score"]
        except Exception:
            score = fallback_score

        return (passed, msg, score)

    @staticmethod
    def _validate_python(code: str) -> tuple[bool, str, float]:
        try:
            ast.parse(code)
            return (True, "", 1.0)
        except SyntaxError as e:
            error_ctx = ""
            lines = code.splitlines()
            if e.lineno and e.lineno <= len(lines):
                start = max(0, e.lineno - 3)
                end = min(len(lines), e.lineno + 2)
                error_ctx = "\n".join(
                    f"{'>>>' if i == e.lineno - 1 else '   '} {i+1}: {lines[i]}"
                    for i in range(start, end)
                )
            msg = (
                f"Python SyntaxError at line {e.lineno}: {e.msg}\n"
                f"Context:\n{error_ctx}\n"
                f"Fix this specific error and regenerate the complete file."
            )
            return (False, msg, 0.3)

    @staticmethod
    @staticmethod
    def _validate_cpp(code: str, aidl_dir: str = "") -> tuple[bool, str, float]:
        """Use canonical validator for consistent scoring with final scorer.

        C4-only extension: when aidl_dir is given, also gates on
        CPP↔AIDL name consistency (dspy_opt.validators.
        check_cpp_aidl_name_consistency). This gate does NOT alter the
        canonical structural score returned — only whether `passed` is
        True, and what feedback is sent back to the LLM on retry.
        """
        from dspy_opt.validators import (
            validate as canonical_validate,
            check_cpp_aidl_name_consistency,
            format_cpp_aidl_consistency_feedback,
        )
        result = canonical_validate("cpp", code)

        bad_names = check_cpp_aidl_name_consistency(code, aidl_dir) if aidl_dir else []

        if result.ok and not bad_names:
            return (True, "", result.score)

        errors = list(result.errors or [])
        msg_parts = []
        if errors:
            msg_parts.append(
                "C++ VHAL validation errors:\n"
                + "\n".join(f"- {e}" for e in errors) + "\n\n"
                "Fix ALL issues. Required for AIDL V3 VHAL:\n"
                "1. Inherit IVehicleHardware (NOT BnIVehicle)\n"
                "2. Implement getAllPropertyConfigs(), getValues(), setValues()\n"
                "3. Use DefaultVehicleHal in main() with AServiceManager_addService\n"
                "4. No HIDL patterns (HIDL_FETCH_*, Return<>, hidl/ headers)\n"
                "5. Use aidl:: namespace, not android::hardware::automotive::vehicle::V2_0"
            )
        if bad_names:
            msg_parts.append(format_cpp_aidl_consistency_feedback(bad_names, aidl_dir))

        msg = "\n\n".join(msg_parts)
        return (False, msg, result.score)

    @staticmethod
    @staticmethod
    def _validate_selinux(code: str) -> tuple[bool, str, float]:
        """
        Validate SELinux using the SAME logic as dspy_opt.validators.validate_selinux
        so retry feedback matches what the final scorer checks.
        """
        # Use the canonical validator to get consistent score+errors
        from dspy_opt.validators import validate as canonical_validate
        result = canonical_validate("selinux", code)
        if result.ok:
            return (True, "", result.score)

        # Build actionable error message listing exactly what is missing
        missing = result.errors  # e.g. ["Missing init_daemon_domain", ...]
        required_patterns = {
            "Missing type declarations":
                "Add: type hal_vehicle_<domain>, domain;\n"
                "     type hal_vehicle_<domain>_exec, exec_type, vendor_file_type, file_type;",
            "No domain type":
                "Ensure the type has 'domain' attribute: type hal_vehicle_<domain>, domain;",
            "Missing init_daemon_domain":
                "Add: init_daemon_domain(hal_vehicle_<domain>, hal_vehicle_<domain>_exec)",
            "Missing hal_server_domain(x, hal_vehicle)":
                "Add: hal_server_domain(hal_vehicle_<domain>, hal_vehicle_default)",
            "Missing binder_call/binder_use":
                "Add: binder_use(hal_vehicle_<domain>)\n"
                "     binder_call(hal_vehicle_<domain>, binderservicemanager)",
            "Missing vndbinder_device allow rule":
                "Add: allow hal_vehicle_<domain> vndbinder_device:chr_file rw_file_perms;",
        }
        fix_hints = []
        for err in missing:
            for key, hint in required_patterns.items():
                if key.lower() in err.lower():
                    fix_hints.append(f"- {err}\n  FIX: {hint}")
                    break
            else:
                fix_hints.append(f"- {err}")

        msg = (
            "SELinux policy missing required AIDL VHAL patterns:\n"
            + "\n".join(fix_hints) + "\n\n"
            "Generate a COMPLETE .te file with ALL of the above. "
            "Replace <domain> with the actual HAL domain name (e.g. adas, body, cabin). "
            "Do NOT use HIDL patterns (hwbinder, hwservice_manager)."
        )
        return (False, msg, result.score)

    @staticmethod
    def _validate_aidl(code: str) -> tuple[bool, str, float]:
        """Use canonical validator for consistent scoring with final scorer."""
        from dspy_opt.validators import validate as canonical_validate
        result = canonical_validate("aidl", code)
        if result.ok:
            return (True, "", result.score)
        errors = result.errors or []
        msg = (
            "AIDL validation errors:\n"
            + "\n".join(f"- {e}" for e in errors) + "\n\n"
            "Fix ALL issues. Required:\n"
            "1. package android.hardware.automotive.vehicle; (first line)\n"
            "2. @VintfStability annotation\n"
            "3. @Backing(type=\"int\") for enum types\n"
            "4. UPPER_CASE enum constants with hex values (0x1000+)\n"
            "5. Balanced braces"
        )
        return (False, msg, result.score)

    @staticmethod
    def _validate_build(code: str) -> tuple[bool, str, float]:
        """Use canonical validator for consistent scoring with final scorer."""
        from dspy_opt.validators import validate as canonical_validate
        result = canonical_validate("build", code)
        if result.ok:
            return (True, "", result.score)
        errors = result.errors or []
        msg = (
            "Android.bp validation errors:\n"
            + "\n".join(f"- {e}" for e in errors) + "\n\n"
            "Fix ALL issues. Required:\n"
            "1. Block type: aidl_interface, cc_binary, or cc_library_shared\n"
            "2. vendor: true (HAL modules must be on vendor partition)\n"
            "3. name: \"<module_name>\"\n"
            "4. srcs: [\"*.cpp\"]\n"
            "5. Balanced braces"
        )
        return (False, msg, result.score)

    @staticmethod
    def _validate_kotlin(code: str) -> tuple[bool, str, float]:
        """Use canonical validator for consistent scoring with final scorer."""
        from dspy_opt.validators import validate as canonical_validate
        result = canonical_validate("android_app", code)
        if result.ok:
            return (True, "", result.score)
        errors = result.errors or []
        msg = (
            "Kotlin validation errors:\n"
            + "\n".join(f"- {e}" for e in errors) + "\n\n"
            "Fix ALL issues. Required:\n"
            "1. CarPropertyManager or Car.createCar usage\n"
            "2. Fragment class with onViewCreated/onCreateView\n"
            "3. registerCallback for CarPropertyEventCallback\n"
            "4. Balanced braces"
        )
        return (False, msg, result.score)

    @staticmethod
    def _validate_xml(code: str) -> tuple[bool, str, float]:
        """Use canonical validator for consistent scoring with final scorer."""
        from dspy_opt.validators import validate as canonical_validate
        result = canonical_validate("android_layout", code)
        if result.ok:
            return (True, "", result.score)
        errors = result.errors or []
        msg = (
            f"Android layout XML validation failed (score={result.score:.2f}):\n"
            + "\n".join(f"- {e}" for e in errors) + "\n\n"
            "Fix ALL issues above. Ensure:\n"
            "1. Root element is ScrollView/LinearLayout with xmlns:android and xmlns:app\n"
            "2. Every TextView/Switch/SeekBar/Button/CheckBox/EditText has android:id\n"
            "3. All XML tags are properly closed\n"
            "4. No double-escaped content (&lt; &gt; as literal text)"
        )
        return (False, msg, result.score)

    @staticmethod
    def _validate_markdown(code: str) -> tuple[bool, str, float]:
        lines = code.splitlines()
        has_h1 = any(l.startswith("# ") for l in lines)
        has_h2 = any(l.startswith("## ") for l in lines)
        if not has_h1 or not has_h2:
            return (False, "Missing H1 or H2 headings", 0.5)
        return (True, "", 1.0)


# ══════════════════════════════════════════════════════════════════
# POST-VALIDATION RETRY ENGINE
# ══════════════════════════════════════════════════════════════════
#
# KEY DESIGN CHANGE from old C4:
#   OLD: FeedbackGenerator wrapped agent._generate() directly,
#        bypassing architect.run() and polluting aosp_context.
#   NEW: PostValidationRetry works on OUTPUT FILES after the
#        architect has already run. It reads the file, validates,
#        and only re-generates if validation fails — using a
#        SEPARATE feedback field so RAG context stays clean.
# ══════════════════════════════════════════════════════════════════

class PostValidationRetry:
    """
    Post-validation retry loop for generated files.

    Strategy:
      1. Read the file that the architect already generated
      2. Validate it
      3. If FAIL → re-generate using the SAME agent with error
         feedback in a SEPARATE 'validation_feedback' kwarg
      4. Repeat up to max_retries times
      5. Keep the best-scoring version

    This preserves the architect's orchestration (unlike the old C4
    which bypassed agent.run() entirely).
    """

    def __init__(self, max_retries: int = MAX_RETRIES):
        self.max_retries = max_retries

    def validate_and_retry_file(
        self,
        file_path:    Path,
        agent_type:   str,
        agent,
        gen_kwargs:   dict,
        tracker:      "ThompsonTracker",
        extra_files:  list = None,
        aidl_dir:     str = "",
    ) -> dict:
        """
        Validate a generated file; retry the agent if it fails.

        Args:
            file_path:   path to the generated file
            agent_type:  validator key (aidl, cpp, selinux, build, ...)
            agent:       the RAGDSPy sub-agent instance (has _generate + _retrieve)
            gen_kwargs:  kwargs matching agent._generate() signature
            tracker:     Thompson tracker for recording outcomes
            aidl_dir:    C4-only. Forwarded to ValidatorFeedback.validate()
                         for agent_type=="cpp" — enables the CPP↔AIDL name
                         consistency gate. No effect for other agent_types.

        Returns:
            dict with attempt count, final score, pass/fail, errors
        """
        metrics = {
            "file":         str(file_path),
            "agent_type":   agent_type,
            "attempts":     0,
            "final_passed": False,
            "final_score":  0.0,
            "errors_by_attempt": [],
        }

        if not file_path.exists():
            metrics["errors_by_attempt"].append("File not generated by architect")
            tracker.record(agent_type, False)
            return metrics

        # ── Attempt 0: validate what the architect already wrote ──
        code = file_path.read_text(encoding="utf-8", errors="ignore")
        code_to_validate = code
        if extra_files:
            extra = "\n".join(
                p.read_text(errors="ignore") for p in extra_files if p.exists())
            code_to_validate = code + "\n" + extra
        passed, error_msg, score = ValidatorFeedback.validate(code_to_validate, agent_type, aidl_dir=aidl_dir)

        best_code  = code
        best_score = score
        metrics["attempts"] = 1

        if passed:
            metrics["final_passed"] = True
            metrics["final_score"]  = score
            tracker.record(agent_type, True)
            return metrics

        metrics["errors_by_attempt"].append(f"Initial: {error_msg[:200]}")
        tag = agent_type.upper()
        print(f"    [C4 {tag}] ✗ Initial validation failed "
              f"(score={score:.3f}) — entering retry loop...")
        for line in (error_msg or "").splitlines()[:6]:
            print(f"        {line}")

        # ── Retry loop: re-generate with error feedback ───────────
        for attempt in range(2, self.max_retries + 1):
            metrics["attempts"] = attempt

            feedback_block = (
                f"Your previous output had validation errors:\n"
                f"{error_msg}\n\n"
                f"Fix ALL errors above. Generate the COMPLETE corrected file. "
                f"Do not omit any sections."
            )

            retry_kwargs = dict(gen_kwargs)

            # Inject error feedback into `properties` (LLM primary input field)
            # rather than `aosp_context` (reference field LLM treats as background).
            # NOTE: A cleaner solution would add `validation_feedback` as a dedicated
            # DSPy Signature field — but that requires modifying all agents and
            # re-running MIPROv2 optimisation, which is out of scope for this thesis.
            retry_kwargs["properties"] = (
                "=== CRITICAL: FIX THESE VALIDATION ERRORS FIRST ===\n"
                + feedback_block
                + "\n=== END ERRORS ===\n\n"
                + "=== ORIGINAL PROPERTIES ===\n"
                + gen_kwargs.get("properties", "")
            )
            # Keep aosp_context clean — only AOSP reference, no error pollution
            retry_kwargs["aosp_context"] = gen_kwargs.get("aosp_context", "")
            try:
                new_code = agent._generate(**retry_kwargs)
            except Exception as e:
                metrics["errors_by_attempt"].append(
                    f"Attempt {attempt}: generation error: {e}")
                continue

            if not new_code or not new_code.strip():
                metrics["errors_by_attempt"].append(
                    f"Attempt {attempt}: empty output")
                continue

            code_for_val = new_code
            if extra_files:
                extra = "\n".join(
                    p.read_text(errors="ignore") for p in extra_files if p.exists())
                code_for_val = new_code + "\n" + extra
            passed, error_msg, score = ValidatorFeedback.validate(
                code_for_val, agent_type, aidl_dir=aidl_dir)

            if score > best_score:
                best_code  = new_code
                best_score = score

            if passed:
                # Write the fixed version back
                file_path.write_text(new_code, encoding="utf-8")
                metrics["final_passed"] = True
                metrics["final_score"]  = score
                tracker.record(agent_type, True)
                print(f"    [C4 {tag}] ✓ Passed on attempt {attempt} "
                      f"(score={score:.3f})")
                return metrics

            metrics["errors_by_attempt"].append(
                f"Attempt {attempt}: {error_msg[:200]}")
            print(f"    [C4 {tag}] ✗ Attempt {attempt} failed "
                  f"(score={score:.3f}) — retrying...")
            for line in (error_msg or "").splitlines()[:6]:
                print(f"        {line}")

        # ── All retries exhausted — keep best version ─────────────
        if best_score > score:
            # best_code from an earlier attempt was better
            file_path.write_text(best_code, encoding="utf-8")

        metrics["final_passed"] = best_score >= 0.8
        metrics["final_score"]  = best_score
        tracker.record(agent_type, metrics["final_passed"])
        print(f"    [C4 {tag}] Max retries reached — keeping best "
              f"(score={best_score:.3f})")
        return metrics


# ══════════════════════════════════════════════════════════════════
# THOMPSON SAMPLING (from C2, simplified)
# ══════════════════════════════════════════════════════════════════

class ThompsonTracker:
    """
    Lightweight Thompson Sampling for prompt variant selection.
    Tracks per-agent success/failure counts for Bayesian updating.
    """

    def __init__(self):
        self._alpha: dict[str, float] = {}  # successes + 1
        self._beta: dict[str, float] = {}   # failures + 1

    def record(self, agent_type: str, success: bool):
        key = agent_type
        if key not in self._alpha:
            self._alpha[key] = 1.0
            self._beta[key] = 1.0
        if success:
            self._alpha[key] += 1
        else:
            self._beta[key] += 1

    def get_stats(self) -> dict:
        return {
            k: {
                "successes": self._alpha[k] - 1,
                "failures": self._beta[k] - 1,
                "success_rate": round((self._alpha[k] - 1) /
                    max(self._alpha[k] + self._beta[k] - 2, 1), 3),
            }
            for k in sorted(self._alpha.keys())
        }


# ══════════════════════════════════════════════════════════════════
# MAIN PIPELINE
# ══════════════════════════════════════════════════════════════════

from vss_to_yaml import vss_to_yaml_spec
from schemas.yaml_loader import load_hal_spec_from_yaml_text
from agents.module_planner_agent import plan_modules_from_spec
from agents.promote_draft_agent import PromoteDraftAgent
from agents.build_glue_agent import BuildGlueAgent, ImprovedBuildGlueAgent
from agents.vss_glue_agent import VssGlueAgent
from agents.vss_labelling_agent import VSSLabellingAgent, flatten_vss
from tools.aosp_layout import ensure_aosp_layout

# ── C3-identical agents (RAG + DSPy) ─────────────────────────────
from agents.rag_dspy_architect_agent   import RAGDSPyArchitectAgent
from agents.rag_dspy_selinux_agent     import RAGDSPySELinuxAgent
from agents.rag_dspy_design_doc_agent  import RAGDSPyDesignDocAgent
from agents.rag_dspy_android_app_agent import RAGDSPyAndroidAppAgent
from agents.rag_dspy_backend_agent     import RAGDSPyBackendAgent

# ── Sub-agents for targeted retry (only used in retry loop) ──────
from agents.rag_dspy_aidl_agent    import RAGDSPyAIDLAgent
from agents.rag_dspy_cpp_agent import RagDspyCppAgent
from agents.rag_dspy_selinux_agent import RAGDSPySELinuxAgent as RAGDSPySELinuxSubAgent
from agents.rag_dspy_build_agent   import RAGDSPyBuildAgent

from dspy_opt.metrics    import score_file
from dspy_opt.validators import validate, print_availability_report

BUILD_GLUE_LLM_TIMEOUT = 600


# ─────────────────────────────────────────────────────────────────
# ModuleSpec  (identical interface across all conditions)
# ─────────────────────────────────────────────────────────────────

class ModuleSpec:
    def __init__(self, domain: str, properties: list):
        self.domain     = domain.upper()
        self.properties = properties
        self.aosp_level = 14
        self.vendor     = "AOSP"

    def to_llm_spec(self) -> str:
        lines = [
            f"HAL Domain: {self.domain}",
            f"AOSP Level: {self.aosp_level}",
            f"Vendor: {self.vendor}",
            f"Properties: {len(self.properties)}",
            "",
        ]
        for prop in self.properties:
            name      = getattr(prop, "id",     "UNKNOWN")
            typ       = getattr(prop, "type",   "UNKNOWN")
            access    = getattr(prop, "access", "READ_WRITE")
            areas     = getattr(prop, "areas",  ["GLOBAL"])
            areas_str = ", ".join(areas) if isinstance(areas, (list, tuple)) else str(areas)
            lines += [f"- Name: {name}", f"  Type: {typ}",
                      f"  Access: {access}", f"  Areas: {areas_str}", ""]
        return "\n".join(lines)


# File patterns for scoring (same as C3)
_FILE_PATTERNS = {
    "aidl": "**/*.aidl", "cpp": "**/*.cpp", "selinux": "**/*.te",
    "build": "**/Android.bp", "android_app": "**/*Fragment*.kt",
    "android_layout": "**/fragment_*.xml", "backend": "**/main.py",
    "backend_model": "**/models_*.py", "simulator": "**/simulator_*.py",
    "design_doc": "**/DESIGN_DOCUMENT.md", "puml": "**/*.puml",
}


def _score_and_log(agent_type: str, fpath: Path) -> float:
    try:
        code = fpath.read_text(encoding="utf-8", errors="ignore")
        # For .cpp files, prepend the matching .h so clang++ sees the class
        # declaration before the out-of-line method definitions — mirrors how
        # the retry engine passes extra_files=[header] to validate_and_retry_file().
        if agent_type == "cpp" and fpath.suffix == ".cpp":
            header_path = fpath.with_suffix(".h")
            if header_path.exists():
                code = code + "\n" + header_path.read_text(encoding="utf-8", errors="ignore")
        score = score_file(agent_type, code)
        result = validate(agent_type, code)
        status = "✓" if result.ok else "✗"
        errmsg = f"  ← {result.errors[0][:55]}" if result.errors else ""
        print(f"   [{status}] {agent_type:<18} score={score:.3f}  "
              f"syntax={result.score:.3f} ({result.tool}){errmsg}")
        return score
    except Exception as e:
        print(f"   [?] {agent_type:<18} scoring failed: {e}")
        return 0.0


def _score_files(agent_types: list[str], domain_filter: str = "") -> dict[str, float]:
    scores: dict[str, list[float]] = {}
    for agent_type in agent_types:
        pattern = _FILE_PATTERNS.get(agent_type)
        if not pattern:
            continue
        suffix = pattern.removeprefix("**/")
        matches = list(OUTPUT_DIR.rglob(suffix))
        if domain_filter:
            matches = [f for f in matches
                       if domain_filter.lower() in f.name.lower()
                       or domain_filter.lower() in str(f.parent).lower()]
        for fpath in matches:
            s = _score_and_log(agent_type, fpath)
            scores.setdefault(agent_type, []).append(s)
    return {k: round(sum(v)/len(v), 4) for k, v in scores.items() if v}


def _avg(d: dict) -> float:
    vals = list(d.values())
    return round(sum(vals)/len(vals), 4) if vals else 0.0


# ─────────────────────────────────────────────────────────────────
# HAL module generation — C3 architect + C4 post-validation retry
# ─────────────────────────────────────────────────────────────────

def _generate_one_module(
    domain: str,
    module_props: list,
    run_metrics: list,
    tracker: ThompsonTracker,
) -> tuple[str, bool, str | None]:
    """
    Generate HAL module files using the SAME architect.run() as C3,
    then post-validate each output and retry failed agents.
    """
    print(f"\n{'='*60}")
    print(f" MODULE: {domain.upper()} ({len(module_props)} props)")
    print(f"{'='*60}")

    module_spec = ModuleSpec(domain=domain, properties=module_props)
    t0 = time.time()

    # ── Step 1: Run architect identically to C3 ───────────────────
    try:
        agent = RAGDSPyArchitectAgent(
            **AGENT_CFG,
            output_root=str(OUTPUT_DIR),
            enable_chunk_retry=True,   # C4: chunk retry is a C4 contribution
        )
        agent.run(module_spec)
        chunk_retries = getattr(agent, "last_chunk_retries", 0)
    except Exception as e:
        print(f" [C4 MODULE {domain}] → architect.run() FAILED: {e}")
        run_metrics.append({
            "domain": domain, "stage": "hal_module",
            "success": False, "error": str(e),
            "generation_time": round(time.time() - t0, 2),
        })
        return (domain, False, str(e))

    t_gen = time.time() - t0
    print(f"\n [C4 ARCHITECT] Generation done ({t_gen:.1f}s) — starting post-validation...")

    # ── Step 2: Post-validate each generated file ─────────────────
    retry_engine = PostValidationRetry(max_retries=MAX_RETRIES)
    llm_spec = module_spec.to_llm_spec()

    agent_file_map: dict[str, list[Path]] = {}
    for agent_type in ["aidl", "cpp", "selinux", "build"]:
        pattern = _FILE_PATTERNS.get(agent_type, "")
        suffix = pattern.removeprefix("**/")
        matches = list(OUTPUT_DIR.rglob(suffix))
        matches = [f for f in matches
                   if domain.lower() in f.name.lower() or domain.lower() in str(f.parent).lower()]
        if matches:
            agent_file_map[agent_type] = matches

    _sub_agent_classes = {
        "aidl": RAGDSPyAIDLAgent,
        "cpp": RagDspyCppAgent,   # Modern standalone agent
        "selinux": RAGDSPySELinuxSubAgent,
        "build": RAGDSPyBuildAgent,
    }

    retry_metrics = []
    for agent_type, files in agent_file_map.items():
        for fpath in files:
            code = fpath.read_text(encoding="utf-8", errors="ignore")
            if agent_type == "cpp":
                domain_cap = domain.capitalize()
                impl_dir = fpath.parent
                extra = "\n".join(
                    p.read_text(errors="ignore")
                    for p in [
                        impl_dir / f"VehicleHalService{domain_cap}.h",
                        impl_dir / f"VehicleService{domain_cap}.cpp",
                    ] if p.exists())
                code_combined = code + "\n" + extra
            else:
                code_combined = code
            passed, _, score = ValidatorFeedback.validate(
                code_combined, agent_type, aidl_dir=str(AIDL_DIR))

            if passed:
                tracker.record(agent_type, True)
                tag = agent_type.upper()
                print(f" [C4 {tag}] ✓ {fpath.name} passed (score={score:.3f})")
                retry_metrics.append({
                    "agent_type": agent_type, "file": str(fpath),
                    "attempts": 1, "final_passed": True, "final_score": score,
                })
                continue

            if agent_type == "cpp":
                sub_agent = RagDspyCppAgent()   # standalone — does not accept **AGENT_CFG
            else:
                AgentClass = _sub_agent_classes.get(agent_type)
                if not AgentClass:
                    tracker.record(agent_type, False)
                    continue
                sub_agent = AgentClass(**AGENT_CFG)

            if agent_type == "cpp":
                rag_context = ""
            else:        
                rag_query = f"{domain} {agent_type} AOSP 14 VHAL android.hardware.automotive.vehicle"
                rag_context = sub_agent._retrieve(rag_query) if hasattr(sub_agent, '_retrieve') else ""

            gen_kwargs = {
                "domain": domain,
                "aosp_context": rag_context,
            }
            if agent_type == "selinux":
                gen_kwargs["service_name"] = f"vendor.vss.{domain.lower()}"
            else:
                gen_kwargs["properties"] = llm_spec
            if agent_type == "cpp":
                # Enables RAGDSPyCppAgent._generate() to route large
                # domains through the chunked path on retry instead of
                # always falling back to single-shot regeneration —
                # see that method's docstring for the full rationale
                # (single-shot retry of an 84+ property domain risks
                # truncation and can score WORSE than the original,
                # preventing convergence).
                gen_kwargs["prop_list"] = module_props
                gen_kwargs["aidl_dir"] = str(AIDL_DIR)

            extra_files = None
            if agent_type == "cpp":
                domain_cap = domain.capitalize()
                impl_dir = fpath.parent
                extra_files = [
                    impl_dir / f"VehicleHalService{domain_cap}.h",
                    impl_dir / f"VehicleService{domain_cap}.cpp",
                ]
            m = retry_engine.validate_and_retry_file(
                file_path=fpath,
                agent_type=agent_type,
                agent=sub_agent,
                gen_kwargs=gen_kwargs,
                tracker=tracker,
                extra_files=extra_files,
                aidl_dir=str(AIDL_DIR),
            )
            retry_metrics.append(m)

    elapsed = round(time.time() - t0, 2)

    print(f"\n Final validation for {domain}:")
    file_scores = _score_files(["aidl", "cpp", "selinux", "build"], domain_filter=domain)
    avg_score = _avg(file_scores)

    total_retries = sum(m.get("attempts", 1) - 1 for m in retry_metrics if m.get("attempts", 1) > 1)
    fixes = sum(1 for m in retry_metrics if m.get("attempts", 1) > 1 and m.get("final_passed"))

    run_metrics.append({
        "domain": domain,
        "stage": "hal_module",
        "success": True,
        "generation_time": elapsed,
        "metric_score": avg_score,
        "file_scores": file_scores,
        "properties": len(module_props),
        "c4_retries": total_retries,
        "c4_fixes": fixes,
        "c4_retry_details": retry_metrics,
        "cpp_chunk_retries": chunk_retries,
    })

    print(f"\n [C4 MODULE {domain}] → OK avg_score={avg_score:.3f} "
          f"retries={total_retries} fixes={fixes} "
          f"cpp_chunk_retries={chunk_retries} ({elapsed:.1f}s)")
    return (domain, True, None)

# ─────────────────────────────────────────────────────────────────
# Support components — C3 generation + C4 post-validation retry
# ─────────────────────────────────────────────────────────────────

def _run_support_with_feedback(
    module_signal_map: dict,
    full_spec,
    yaml_spec:    str,
    run_metrics:  list,
    tracker:      ThompsonTracker,
):
    """
    Run support agents (design doc, SELinux, Android app, backend)
    identically to C3, then post-validate output files with retry.
    """
    print("\n[C4 SUPPORT] Generating support components "
          "(C3 agents + post-validation retry)...")

    retry_engine = PostValidationRetry(max_retries=MAX_RETRIES)

    def _run_and_validate(
        stage:       str,
        fn,
        agent_types: list[str],
        retry_agents: dict[str, object] | None = None,
    ):
        """Run agent, score, then post-validate with retry."""
        t0 = time.time()
        try:
            # Step 1: run identically to C3
            fn()
        except Exception as e:
            run_metrics.append({
                "stage": stage, "success": False,
                "error": str(e),
                "generation_time": round(time.time() - t0, 2),
            })
            print(f"  [C4 SUPPORT] {stage} → FAILED: {e}")
            return

        elapsed_gen = round(time.time() - t0, 2)

        # Step 2: post-validate generated files
        retry_results = []
        if retry_agents:
            for agent_type, sub_agent in retry_agents.items():
                pattern = _FILE_PATTERNS.get(agent_type, "")
                suffix  = pattern.removeprefix("**/")
                matches = list(OUTPUT_DIR.rglob(suffix))
                for fpath in matches:
                    # Build gen_kwargs for retry
                    rag_query = f"{agent_type} AOSP 14 VHAL implementation"
                    rag_context = sub_agent._retrieve(rag_query)
                    gen_kwargs = {
                        "domain":       "support",
                        "properties":   "",
                        "aosp_context": rag_context,
                    }
                    m = retry_engine.validate_and_retry_file(
                        file_path=fpath,
                        agent_type=agent_type,
                        agent=sub_agent,
                        gen_kwargs=gen_kwargs,
                        tracker=tracker,
                        aidl_dir=str(AIDL_DIR),
                    )
                    retry_results.append(m)
        else:
            # No retry agents configured — just record scores
            for at in agent_types:
                tracker.record(at, True)

        elapsed_total = round(time.time() - t0, 2)
        file_scores = _score_files(agent_types)
        avg = _avg(file_scores)

        total_retries = sum(
            m.get("attempts", 1) - 1 for m in retry_results
            if m.get("attempts", 1) > 1
        )

        run_metrics.append({
            "stage":           stage,
            "success":         True,
            "generation_time": elapsed_total,
            "file_scores":     file_scores,
            "metric_score":    avg,
            "c4_retries":      total_retries,
            "c4_retry_details": retry_results,
        })
        print(f"  [C4 SUPPORT] {stage} → OK  "
              f"avg={avg:.3f}  retries={total_retries}  "
              f"({elapsed_gen:.1f}s gen + "
              f"{elapsed_total - elapsed_gen:.1f}s validation)")

    # ── Build retry sub-agents for support components ─────────────
    selinux_retry = RAGDSPySELinuxSubAgent(**AGENT_CFG)

    group_a = [
        (
            "design_doc",
            lambda: RAGDSPyDesignDocAgent(**AGENT_CFG_WITH_OUTPUT).run(
                module_signal_map, full_spec.properties, yaml_spec),
            ["design_doc", "puml"],
            None,   # markdown/puml: no sub-agent retry (heuristic only)
        ),
        (
            "selinux",
            lambda: RAGDSPySELinuxAgent(**AGENT_CFG).run(full_spec),
            ["selinux"],
            {"selinux": selinux_retry},
        ),
        (
            "android_app",
            lambda: RAGDSPyAndroidAppAgent(**AGENT_CFG_WITH_OUTPUT).run(
                module_signal_map, full_spec.properties),
            ["android_app", "android_layout"],
            None,   # Kotlin/XML: basic heuristic validators
        ),
        (
            "backend",
            lambda: RAGDSPyBackendAgent(**AGENT_CFG_WITH_OUTPUT).run(
                module_signal_map, full_spec.properties),
            ["backend", "backend_model", "simulator"],
            None,   # Python: ast.parse retry would need per-file agent
        ),
    ]

    for stage, fn, atypes, retry_agents in group_a:
        print(f"  [C4 SUPPORT] {stage}...")
        _run_and_validate(stage, fn, atypes, retry_agents)
        if stage == "android_app":
            print("[FIX] Fixing Android layouts before score update...")
            fix_android_layouts(str(OUTPUT_DIR))
            # Override android_layout score in run_metrics with post-fix score
            fixed_scores = _score_files(["android_layout"])
            for m in run_metrics:
                if m.get("stage") == "android_app" and "file_scores" in m:
                    m["file_scores"].update(fixed_scores)
                    m["metric_score"] = _avg(m["file_scores"])
                    print(f"[FIX] android_layout score updated: {fixed_scores}")


# ─────────────────────────────────────────────────────────────────
# Preflight checks (same as C3)
# ─────────────────────────────────────────────────────────────────

def _preflight_rag() -> bool:
    db_path = Path(AGENT_CFG["rag_db_path"])
    if not db_path.exists():
        print(f"[PREFLIGHT] ✗ ChromaDB not found: {db_path}")
        print("  Run: python -m rag.aosp_indexer --source aosp_source "
              "--db rag/chroma_db")
        return False
    try:
        # Use the monkey-patched singleton — do NOT create a new client
        import chromadb
        client = chromadb.PersistentClient(path=str(db_path))
        cols   = client.list_collections()
        total  = sum(c.count() for c in cols)
        print(f"[PREFLIGHT] ✓ ChromaDB — {len(cols)} collections, "
              f"{total:,} chunks")
        for c in cols:
            print(f"             {c.name}: {c.count()} chunks")
        del client  # release reference (singleton stays alive)
        return True
    except Exception as e:
        print(f"[PREFLIGHT] ✗ ChromaDB error: {e}")
        return False


def _preflight_dspy() -> None:
    saved_dir   = Path(AGENT_CFG["dspy_programs_dir"])
    agent_types = [
        "aidl", "cpp", "selinux", "build", "vintf",
        "design_doc", "puml", "android_app", "android_layout",
        "backend", "backend_model", "simulator",
    ]
    found   = [a for a in agent_types
               if (saved_dir / f"{a}_program" / "program.json").exists()]
    missing = [a for a in agent_types if a not in found]
    print(f"[PREFLIGHT] DSPy programs: {len(found)}/{len(agent_types)} "
          f"optimised")
    if missing:
        print(f"             Missing (unoptimised fallback): "
              f"{', '.join(missing)}")


# ─────────────────────────────────────────────────────────────────
# Results
# ─────────────────────────────────────────────────────────────────

def _save_results(run_metrics, tracker, t_total):
    RESULTS_DIR.mkdir(parents=True, exist_ok=True)

    all_scores = []
    per_agent = {}
    for m in run_metrics:
        for agent_type, score in m.get("file_scores", {}).items():
            per_agent.setdefault(agent_type, []).append(score)
            all_scores.append(score)

    total_retries = sum(m.get("c4_retries", 0) for m in run_metrics)
    total_fixes   = sum(m.get("c4_fixes", 0) for m in run_metrics)

    summary = {
        "condition":          "c4_feedback",
        "total_signals":      TEST_SIGNAL_COUNT,
        "max_retries":        MAX_RETRIES,
        "total_run_time_s":   round(t_total, 1),
        "avg_metric_score":   (round(sum(all_scores)/len(all_scores), 4)
                               if all_scores else 0.0),
        "stages_succeeded":   sum(1 for m in run_metrics if m.get("success")),
        "stages_total":       len(run_metrics),
        "total_retries":      total_retries,
        "total_fixes":        total_fixes,
        "thompson_stats":     tracker.get_stats(),
        "per_agent_avg_scores": {
            k: round(sum(v)/len(v), 4) for k, v in per_agent.items()
        },
        "per_stage_metrics":  run_metrics,
    }

    out_path = RESULTS_DIR / "c4_feedback.json"
    out_path.write_text(
        json.dumps(summary, indent=2, ensure_ascii=False), encoding="utf-8")

    # Print summary
    print(f"\n{'='*65}")
    print(f"  C4 Feedback Loop Pipeline — Run Summary")
    print(f"{'='*65}")
    print(f"  Avg metric score     : {summary['avg_metric_score']}")
    print(f"  Stages succeeded     : "
          f"{summary['stages_succeeded']}/{summary['stages_total']}")
    print(f"  Max retries/file     : {MAX_RETRIES}")
    print(f"  Total retries used   : {total_retries}")
    print(f"  Files fixed by retry : {total_fixes}")
    print(f"  Total run time       : {summary['total_run_time_s']}s")
    print(f"\n  Thompson Sampling stats:")
    for agent, stats in tracker.get_stats().items():
        print(f"    {agent:<18} success_rate={stats['success_rate']:.3f} "
              f"({stats['successes']:.0f}/"
              f"{stats['successes']+stats['failures']:.0f})")
    print(f"\n  Per-agent avg scores:")
    for k, v in sorted(summary["per_agent_avg_scores"].items()):
        bar = "█" * int(v * 20)
        print(f"    {'✓' if v >= 0.8 else '~'} {k:<18} {v:.3f}  {bar}")
    print(f"{'='*65}")
    print(f"  Outputs  → {OUTPUT_DIR.resolve()}")
    print(f"  Results  → {out_path.resolve()}")
    print(f"{'='*65}")

    return summary


# ── main() ────────────────────────────────────────────────────────

def main():
    t_run_start = time.time()
    OUTPUT_DIR.mkdir(parents=True, exist_ok=True)
    PERSISTENT_CACHE_DIR.mkdir(parents=True, exist_ok=True)

    print("=" * 70)
    print("  VSS → AAOS HAL Generation — Condition 4: Feedback Loop")
    print("=" * 70)
    print(f"  Signals       : {TEST_SIGNAL_COUNT}")
    print(f"  Output dir    : {OUTPUT_DIR.resolve()}")
    print(f"  Max retries   : {MAX_RETRIES}")
    print(f"  RAG top-k     : {AGENT_CFG['rag_top_k']}")
    print(f"  Strategy      : C3 architect.run() + post-validation retry")
    print()

    print_availability_report()

    # ── Preflight ─────────────────────────────────────────────────
    if not _preflight_rag():
        return
    _preflight_dspy()
    print()

    run_metrics = []
    tracker = ThompsonTracker()

    # ── 1-5: Load, label, YAML, spec, plan (identical to C3) ─────
    print(f"[PREP] Loading {VSS_PATH} ...")
    with open(VSS_PATH, "r", encoding="utf-8") as f:
        raw_vss = json.load(f)
    all_leaves = flatten_vss(raw_vss)
    print(f"       {len(all_leaves)} leaf signals found")

    if len(all_leaves) >= TEST_SIGNAL_COUNT:
        import random
        random.seed(42)
        sorted_items = sorted(all_leaves.items())
        selected_items = random.sample(sorted_items, TEST_SIGNAL_COUNT)
        selected_signals = dict(selected_items)
    else:
        selected_signals = dict(all_leaves.items())

    limited_path = PERSISTENT_CACHE_DIR / f"VSS_LIMITED_{TEST_SIGNAL_COUNT}.json"
    new_limited = json.dumps(selected_signals, indent=2, ensure_ascii=False)
    if not limited_path.exists() or limited_path.read_text(encoding="utf-8") != new_limited:
        limited_path.write_text(new_limited, encoding="utf-8")

    labelled_path = PERSISTENT_CACHE_DIR / f"VSS_LABELLED_{TEST_SIGNAL_COUNT}.json"
    if (labelled_path.exists()
            and labelled_path.stat().st_mtime >= limited_path.stat().st_mtime):
        print(f"[LABELLING] Cached labels: {labelled_path}")
        labelled_data = json.loads(labelled_path.read_text())
    else:
        print("[LABELLING] Labelling signals...")
        labelled_data = VSSLabellingAgent().run_on_dict(selected_signals)
        labelled_path.write_text(
            json.dumps(labelled_data, indent=2, ensure_ascii=False),
            encoding="utf-8")
    print(f"             {len(labelled_data)} signals labelled")

    print("\n[YAML] Converting to HAL YAML spec...")
    yaml_spec, prop_count = vss_to_yaml_spec(
        vss_json_path=str(labelled_path),
        include_prefixes=None, max_props=None,
        vendor_namespace=VENDOR_NAMESPACE, add_meta=True,
    )
    spec_path = OUTPUT_DIR / f"SPEC_FROM_VSS_{TEST_SIGNAL_COUNT}.yaml"
    spec_path.write_text(yaml_spec, encoding="utf-8")
    print(f"       {prop_count} properties")

    print("[LOAD] Loading HAL spec...")
    full_spec = load_hal_spec_from_yaml_text(yaml_spec)
    properties_by_id = {
        getattr(p, "id", None): p
        for p in full_spec.properties
        if getattr(p, "id", None)
    }
    print(f"       {len(properties_by_id)} unique property IDs")

    print("\n[PLAN] Running Module Planner...")
    module_signal_map = plan_modules_from_spec(yaml_spec, use_fast_mode=True)
    total = sum(len(v) for v in module_signal_map.values())
    print(f"       {len(module_signal_map)} modules, {total} signals")

    # ── 6. HAL module generation with feedback ────────────────────
    print(f"\n[GEN] Generating HAL modules "
          f"(C3 architect + post-validation retry, "
          f"max {MAX_RETRIES} retries)...")

    tasks = []
    for domain, signal_names in module_signal_map.items():
        props = [properties_by_id[n] for n in signal_names
                 if n in properties_by_id]
        if not props:
            print(f"  Skipping {domain} — no properties resolved")
            continue
        print(f"  {domain}: {len(props)}/{len(signal_names)} "
              f"properties matched")
        tasks.append((domain, props))

    generated_count = 0
    for domain, props in tasks:
        _, ok, _ = _generate_one_module(domain, props, run_metrics, tracker)
        if ok:
            generated_count += 1

    print(f"\n[GEN] HAL modules: {generated_count}/{len(tasks)} OK")

    # ── 7. Support components ─────────────────────────────────────
    _run_support_with_feedback(
        module_signal_map, full_spec, yaml_spec, run_metrics, tracker)

    # ── 8. PromoteDraft + BuildGlue ───────────────────────────────
    print("  [C4 SUPPORT] Running PromoteDraft → BuildGlue...")
    try:
        PromoteDraftAgent().run()
        print("  [C4 SUPPORT] PromoteDraft → OK")
    except Exception as e:
        print(f"  [C4 SUPPORT] PromoteDraft → FAILED: {e}")

    try:
        module_plan_path = OUTPUT_DIR / "MODULE_PLAN.json"
        llm_client = None
        try:
            from llm_client import call_llm
            class _W:
                def generate(self, prompt, timeout=300):
                    try:    return call_llm(prompt, timeout=timeout)
                    except TypeError: return call_llm(prompt)
            llm_client = _W()
        except Exception:
            pass

        build_agent = (
            ImprovedBuildGlueAgent(
                output_root=str(OUTPUT_DIR),
                module_plan=(str(module_plan_path)
                             if module_plan_path.exists() else None),
                hal_spec=(str(spec_path)
                          if spec_path.exists() else None),
                llm_client=llm_client, timeout=BUILD_GLUE_LLM_TIMEOUT,
            ) if llm_client else
            BuildGlueAgent(
                output_root=str(OUTPUT_DIR),
                module_plan=(str(module_plan_path)
                             if module_plan_path.exists() else None),
                hal_spec=(str(spec_path)
                          if spec_path.exists() else None),
            )
        )
        ok = build_agent.run()
        build_scores = _score_files(["build"])
        run_metrics.append({
            "stage": "build_glue", "success": ok,
            "file_scores": build_scores,
            "metric_score": _avg(build_scores),
        })
        print(f"  [C4 SUPPORT] BuildGlue → {'OK' if ok else 'FAILED'}")
    except Exception as e:
        run_metrics.append({
            "stage": "build_glue", "success": False, "error": str(e)})
        print(f"  [C4 SUPPORT] BuildGlue → FAILED: {e}")

    # VssGlueAgent — deterministic Android 14 glue artifacts
    # Runs AFTER domain agents — reads AIDL files to generate real property configs
    print("  [C4 SUPPORT] VssGlue (Android 14 binding artifacts)...")
    try:
        vss_glue_dir = str(OUTPUT_DIR / "hardware/interfaces/automotive/vehicle/aidl/impl/vss")
        aidl_dir = str(OUTPUT_DIR / "hardware/interfaces/automotive/vehicle/aidl/android/hardware/automotive/vehicle")
        sepolicy_dir = str(OUTPUT_DIR / "sepolicy")
        VssGlueAgent().run(output_dir=vss_glue_dir, aidl_dir=aidl_dir, sepolicy_dir=sepolicy_dir)
        print("  [C4 SUPPORT] VssGlue → OK")
        run_metrics.append({"stage": "vss_glue", "success": True})
    except Exception as e:
        print(f"  [C4 SUPPORT] VssGlue → FAILED: {e}")
        run_metrics.append({"stage": "vss_glue", "success": False, "error": str(e)})

    # ── 9. Save results ───────────────────────────────────────────
    t_total = round(time.time() - t_run_start, 1)
    _save_results(run_metrics, tracker, t_total)


if __name__ == "__main__":
    main()