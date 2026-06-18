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

# ── Configuration ─────────────────────────────────────────────────

MAX_RETRIES        = 3     # retry attempts per file on validation failure
OUTPUT_DIR         = Path("output_c4_feedback")
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
    def validate(code: str, agent_type: str) -> tuple[bool, str, float]:
        """
        Validate code and return (passed, error_message, score).
        error_message is formatted for LLM consumption.
        """
        if agent_type == "aidl":
            return ValidatorFeedback._validate_aidl(code)
        elif agent_type == "cpp":
            return ValidatorFeedback._validate_cpp(code)
        elif agent_type == "selinux":
            return ValidatorFeedback._validate_selinux(code)
        elif agent_type == "build":
            return ValidatorFeedback._validate_build(code)
        elif agent_type in ("backend", "backend_model", "simulator"):
            return ValidatorFeedback._validate_python(code)
        elif agent_type in ("android_app",):
            return ValidatorFeedback._validate_kotlin(code)
        elif agent_type in ("android_layout",):
            return ValidatorFeedback._validate_xml(code)
        elif agent_type == "design_doc":
            return ValidatorFeedback._validate_markdown(code)
        return (True, "", 1.0)

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
    def _validate_cpp(code: str) -> tuple[bool, str, float]:
        # ── Stage 1: AIDL V3 contract check ─────────────────────────────────
        # clang -fsyntax-only passes HIDL-contaminated code (no AOSP headers).
        # Check AIDL violations first so the retry prompt is specific and actionable.
        if not code or not code.strip():
            return (False,
                    "Generated cpp_code is empty. Check DSPY_OUTPUT_FIELD='cpp_impl' "
                    "matches ModernCppVehicleHardwareSignature output fields.", 0.0)

        _HIDL_BANNED = [
            ("HIDL_FETCH_",
             "HIDL_FETCH_* found — forbidden in AIDL HAL. "
             "Remove extern C factory. Register via AServiceManager_addService in main()."),
            ("hidl/",
             "#include <hidl/...> found. Remove all HIDL headers."),
            ("Return<",
             "Return<> type found — HIDL-only. AIDL uses ndk::ScopedAStatus or StatusCode."),
            (".valueType",
             ".valueType used on VehiclePropConfig — this field does not exist in V3. "
             "Type is encoded in the property ID bits."),
            ("BnIVehicle",
             "BnIVehicle used — this name does not exist. "
             "Inherit IVehicleHardware (vendor seam) not BnVehicle (binder layer)."),
        ]
        aidl_violations = []
        for marker, explanation in _HIDL_BANNED:
            if marker in code:
                aidl_violations.append(explanation)

        if "IVehicleHardware" not in code:
            aidl_violations.append(
                "Missing IVehicleHardware base class. Inherit IVehicleHardware (vendor seam). "
                "DefaultVehicleHal (instantiated in main()) owns BnVehicle.")
        if "DefaultVehicleHal" not in code:
            aidl_violations.append(
                "Missing DefaultVehicleHal. Required: "
                "auto vhal = ndk::SharedRefBase::make<DefaultVehicleHal>(std::move(hw)); "
                "AServiceManager_addService(vhal->asBinder().get(), instance.c_str());")
        if "AServiceManager_addService" not in code:
            aidl_violations.append(
                "Missing AServiceManager_addService — AIDL registration, not HIDL_FETCH_*.")
        if "GetValuesCallback" not in code or "GetValueRequest" not in code:
            aidl_violations.append(
                "getValues must be async: StatusCode getValues("
                "std::shared_ptr<const GetValuesCallback>, "
                "const std::vector<GetValueRequest>&) const override. "
                "Invoke (*callback)(results) then return StatusCode::OK.")
        if "SetValuesCallback" not in code or "SetValueRequest" not in code:
            aidl_violations.append(
                "setValues must be async: StatusCode setValues("
                "std::shared_ptr<const SetValuesCallback>, "
                "const std::vector<SetValueRequest>&) override.")

        if aidl_violations:
            msg = (
                "AIDL V3 contract violations — fix ALL before anything else:\n"
                + "\n".join(f"  [{i+1}] {v}" for i, v in enumerate(aidl_violations))
                + "\n\nRegenerate the complete file following IVehicleHardware + "
                "DefaultVehicleHal architecture."
            )
            return (False, msg, max(0.1, 1.0 - len(aidl_violations) * 0.15))

        # ── Stage 2: clang++ syntax check ────────────────────────────────────
        try:
            import tempfile
            tmp = Path(tempfile.mktemp(suffix=".cpp"))
            tmp.write_text(code)
            result = subprocess.run(
                ["clang++", "-fsyntax-only", "-std=c++17", str(tmp)],
                capture_output=True, timeout=15
            )
            tmp.unlink(missing_ok=True)
            if result.returncode == 0:
                return (True, "", 1.0)
            stderr = result.stderr.decode(errors="replace")
            real_errors = [l for l in stderr.splitlines()
                           if "error:" in l and "file not found" not in l]
            if not real_errors:
                return (True, "", 0.9)
            msg = (
                f"C++ compilation errors ({len(real_errors)} errors):\n"
                + "\n".join(real_errors[:5]) + "\n"
                "Fix these errors. Keep all #include directives. Regenerate the complete file."
            )
            return (False, msg, max(0, 1.0 - len(real_errors) * 0.15))
        except FileNotFoundError:
            issues = []
            if code.count("{") != code.count("}"):
                issues.append(f"Unbalanced braces: {code.count('{')} open, {code.count('}')} close")
            if code.count("(") != code.count(")"):
                issues.append("Unbalanced parentheses")
            if issues:
                return (False, "C++ issues:\n" + "\n".join(issues), 0.6)
            return (True, "", 0.8)
        except subprocess.TimeoutExpired:
            return (False, "Compilation timed out", 0.5)

    @staticmethod
    def _validate_selinux(code: str) -> tuple[bool, str, float]:
        try:
            import tempfile
            tmp = Path(tempfile.mktemp(suffix=".te"))
            tmp.write_text(code)
            result = subprocess.run(
                ["checkpolicy", "-M", "-c", "30", "-o", "/dev/null", str(tmp)],
                capture_output=True, timeout=10
            )
            tmp.unlink(missing_ok=True)
            if result.returncode == 0:
                return (True, "", 1.0)
            stderr = result.stderr.decode(errors="replace")
            msg = (
                f"SELinux policy compilation failed:\n{stderr[:500]}\n"
                f"Fix the policy syntax errors. Every type declaration must end with semicolon. "
                f"Every allow rule must have the format: allow source target:class permission;"
            )
            return (False, msg, 0.3)
        except FileNotFoundError:
            issues = []
            for i, line in enumerate(code.splitlines(), 1):
                line = line.strip()
                if not line or line.startswith("#"):
                    continue
                if line.startswith(("type ", "allow ", "neverallow ")):
                    if not line.endswith(";"):
                        issues.append(f"Line {i}: missing semicolon: {line[:60]}")
            if issues:
                return (False, "SELinux issues:\n" + "\n".join(issues[:5]), 0.5)
            return (True, "", 0.8)
        except subprocess.TimeoutExpired:
            return (False, "checkpolicy timed out", 0.5)

    @staticmethod
    def _validate_aidl(code: str) -> tuple[bool, str, float]:
        issues = []
        pkg_match = re.search(
                r"^\s*package\s+[\w.]+\s*;", code, re.MULTILINE)
        if not pkg_match:
            issues.append(
                "Missing package declaration — add e.g. 'package android.hardware.automotive.vehicle;' "
                "as the very first statement (before @VintfStability and enum/interface)")
        else:
            type_before = re.search(
                r"(interface|enum|parcelable)\s+\w+", code[:pkg_match.start()])
            if type_before:
                issues.append(
                    "Package declaration must be the FIRST statement — move 'package ...' "
                    "before @VintfStability and before enum/interface declarations")
        if not re.search(r"(interface|parcelable|enum)\s+\w+", code):
            issues.append("Missing interface, enum, or parcelable declaration")
        if code.count("{") != code.count("}"):
            issues.append(f"Unbalanced braces: {code.count('{')} open, {code.count('}')} close")
        if "@VintfStability" not in code:
            issues.append("Missing @VintfStability annotation (required for AAOS VHAL)")
        if issues:
            msg = "AIDL validation errors:\n" + "\n".join(f"- {i}" for i in issues)
            msg += "\nFix these issues and regenerate the complete AIDL file."
            return (False, msg, max(0, 1.0 - len(issues) * 0.2))
        return (True, "", 1.0)

    @staticmethod
    def _validate_build(code: str) -> tuple[bool, str, float]:
        issues = []
        if code.count("{") != code.count("}"):
            issues.append("Unbalanced braces")
        if "vendor:" not in code and "vendor :" not in code:
            issues.append("Missing 'vendor: true' — required for VHAL HAL modules")
        if "name:" not in code and '"name"' not in code:
            issues.append("Missing 'name' field")
        if "srcs:" not in code and '"srcs"' not in code:
            issues.append("Missing 'srcs' field")
        if issues:
            msg = "Android.bp validation errors:\n" + "\n".join(f"- {i}" for i in issues)
            msg += "\nFix these and regenerate the complete Android.bp file."
            return (False, msg, max(0, 1.0 - len(issues) * 0.2))
        return (True, "", 1.0)

    @staticmethod
    def _validate_kotlin(code: str) -> tuple[bool, str, float]:
        issues = []
        if code.count("{") != code.count("}"):
            issues.append(f"Unbalanced braces: {code.count('{')} open, {code.count('}')} close")
        if "class " not in code and "fun " not in code:
            issues.append("No class or function definitions found")
        if issues:
            return (False, "Kotlin issues:\n" + "\n".join(issues), 0.6)
        return (True, "", 0.9)

    @staticmethod
    def _validate_xml(code: str) -> tuple[bool, str, float]:
        import xml.etree.ElementTree as ET
        try:
            ET.fromstring(code)
            return (True, "", 1.0)
        except ET.ParseError as e:
            msg = (
                f"XML parse error: {e}\n"
                f"Fix the XML syntax. Ensure all tags are properly closed, "
                f"attributes are quoted, and special characters are escaped."
            )
            return (False, msg, 0.3)

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
    ) -> dict:
        """
        Validate a generated file; retry the agent if it fails.

        Args:
            file_path:   path to the generated file
            agent_type:  validator key (aidl, cpp, selinux, build, ...)
            agent:       the RAGDSPy sub-agent instance (has _generate + _retrieve)
            gen_kwargs:  kwargs matching agent._generate() signature
            tracker:     Thompson tracker for recording outcomes

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
        passed, error_msg, score = ValidatorFeedback.validate(code_to_validate, agent_type)

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

        # ── Retry loop: re-generate with error feedback ───────────
        for attempt in range(2, self.max_retries + 1):
            metrics["attempts"] = attempt

            # Build feedback as a SEPARATE field — do NOT pollute aosp_context
            feedback_block = (
                f"\n\nYour previous output had validation errors:\n"
                f"{error_msg}\n\n"
                f"Fix ALL errors above. Generate the COMPLETE corrected file. "
                f"Do not omit any sections."
            )

            retry_kwargs = dict(gen_kwargs)
            # DSPy silently ignores unknown kwargs instead of raising TypeError,
            # so the old validation_feedback approach was a no-op for all DSPy
            # agents (selinux, build, vintf). Fix: always prepend feedback to
            # aosp_context so the LLM actually sees the error on every retry.
            retry_kwargs["aosp_context"] = (
                "=== VALIDATION FAILED — FIX THESE ERRORS BEFORE ANYTHING ELSE ===\n"
                + feedback_block
                + "\n=== END ERRORS ===\n\n"
                + "=== AOSP REFERENCE CONTEXT ===\n"
                + gen_kwargs.get("aosp_context", "")
            )
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
                code_for_val, agent_type)

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
        code  = fpath.read_text(encoding="utf-8", errors="ignore")
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
        agent = RAGDSPyArchitectAgent(**AGENT_CFG, output_root=str(OUTPUT_DIR))
        agent.run(module_spec)
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
            passed, _, score = ValidatorFeedback.validate(code_combined, agent_type)

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
                "properties": llm_spec,
                "aosp_context": rag_context,
            }
            if agent_type == "selinux":
                gen_kwargs["service_name"] = f"vendor.vss.{domain.lower()}"

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
    })

    print(f"\n [C4 MODULE {domain}] → OK avg_score={avg_score:.3f} "
          f"retries={total_retries} fixes={fixes} ({elapsed:.1f}s)")
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
        VssGlueAgent().run(output_dir=vss_glue_dir, aidl_dir=aidl_dir)
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