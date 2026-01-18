# FILE: agents/vhal_aidl_agent.py

import json
import re
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

from llm_client import call_llm
from tools.safe_writer import SafeWriter


class VHALAidlAgent:
    """
    Two-Phase Generation (Option C), implemented WITHOUT changing your project layout/design.

    Phase 1 (LLM): produce a SMALL 'plan' JSON (behavioral intent only).
      - This is reliable and easy to validate.

    Phase 2 (Deterministic): emit AOSP-compliant AIDL deterministically.
      - No LLM-generated AOSP files.
      - Plan can influence only safe, local decisions (e.g., future extensions).

    Backward compatible:
      - run(spec_text: str) still works (no plan) and will generate deterministic AIDL.
      - run(spec_text: str, plan: dict) also works.
    """

    def __init__(self):
        self.name = "VHAL AIDL Agent"
        self.output_root = "output"
        self.writer = SafeWriter(self.output_root)

        self.raw_dir = Path(self.output_root)
        self.raw_dir.mkdir(parents=True, exist_ok=True)

        # Phase-1: JSON-only plan contract (LLM)
        self.system = (
            "You are an Android Automotive HAL planning assistant.\n"
            "Output STRICT JSON only.\n"
            "No prose. No markdown. No code fences.\n"
            "If you cannot comply, output exactly: {\"plan\": {\"ok\": false, \"reason\": \"cannot_comply\"}}\n"
        )

        self.base_dir = "hardware/interfaces/automotive/vehicle/aidl"
        self.pkg_dir = f"{self.base_dir}/android/hardware/automotive/vehicle"

        self.required_files = [
            f"{self.pkg_dir}/IVehicle.aidl",
            f"{self.pkg_dir}/IVehicleCallback.aidl",
            f"{self.pkg_dir}/VehiclePropValue.aidl",
        ]

    # ---------------------------------------------------------------------
    # Phase 1: LLM plan (small JSON, reliable)
    # ---------------------------------------------------------------------
    def build_plan_prompt(self, spec_text: str) -> str:
        return f"""
OUTPUT CONTRACT (MANDATORY):
Return ONLY valid JSON with this schema:

{{
  "plan": {{
    "ok": true,
    "domain": "HVAC",
    "aosp_level": 14,
    "vendor": "AOSP",
    "notes": "optional short string"
  }}
}}

HARD RULES:
- Output ONLY JSON. No other text.
- NO markdown, NO code fences, NO headings.
- Do NOT generate any files.
- Keep it small and deterministic.

SPEC CONTEXT (do not repeat):
{spec_text}

RETURN JSON NOW.
""".lstrip()

    def _get_llm_plan(self, spec_text: str) -> Optional[Dict[str, Any]]:
        """
        Try to obtain a plan from LLM. If anything is invalid, return None.
        This keeps behavior deterministic and avoids brittle file-generation contracts.
        """
        prompt = self.build_plan_prompt(spec_text)

        out1 = call_llm(prompt, system=self.system, stream=False, temperature=0.0) or ""
        self._dump_raw(out1, "PLAN_attempt1")
        plan1 = self._parse_plan_json(out1)
        if plan1:
            return plan1

        # Repair attempt
        repair = (
            prompt
            + "\nREPAIR (MANDATORY):\n"
              "- Your previous output was INVALID.\n"
              "- Output ONLY JSON exactly matching the schema.\n"
              "- Do NOT include markdown or any explanation.\n"
              "\nPREVIOUS OUTPUT (for correction, do not repeat):\n"
              f"{out1}\n"
        )
        out2 = call_llm(repair, system=self.system, stream=False, temperature=0.0) or ""
        self._dump_raw(out2, "PLAN_attempt2")
        plan2 = self._parse_plan_json(out2)
        return plan2

    def _parse_plan_json(self, text: str) -> Optional[Dict[str, Any]]:
        t = (text or "").strip()
        if not t or not t.startswith("{"):
            return None
        low = t.lower()
        if "```" in t or "\n###" in t:
            return None

        try:
            data = json.loads(t)
        except Exception:
            return None

        plan = data.get("plan")
        if not isinstance(plan, dict):
            return None

        ok = plan.get("ok")
        if ok is not True:
            return None

        # Minimal required fields (keep loose; this is intent only)
        for k in ("domain", "aosp_level", "vendor"):
            if k not in plan:
                return None

        # Normalize types
        try:
            plan["aosp_level"] = int(plan["aosp_level"])
        except Exception:
            return None

        plan["domain"] = str(plan["domain"]).strip() or "UNKNOWN"
        plan["vendor"] = str(plan["vendor"]).strip() or "UNKNOWN"
        if "notes" in plan and plan["notes"] is not None:
            plan["notes"] = str(plan["notes"])[:200]

        return plan

    # ---------------------------------------------------------------------
    # Phase 2: Deterministic emit (AOSP-compliant)
    # ---------------------------------------------------------------------
    def run(self, spec_text: str, plan: Optional[Dict[str, Any]] = None) -> str:
        print(f"[DEBUG] {self.name}: start", flush=True)

        # If plan not provided, try to get one. If plan fails, proceed deterministically anyway.
        if plan is None:
            plan = self._get_llm_plan(spec_text)

        # Deterministic emission (OEM-grade): always produce required files exactly.
        self._write_deterministic_aidl(plan=plan)
        print(f"[DEBUG] {self.name}: deterministic AIDL written", flush=True)

        # Return a small trace string; your orchestrator can log it.
        if plan:
            return json.dumps({"phase": "AIDL", "mode": "two_phase", "plan": plan}, ensure_ascii=False)
        return json.dumps({"phase": "AIDL", "mode": "two_phase", "plan": None}, ensure_ascii=False)

    def _write_deterministic_aidl(self, plan: Optional[Dict[str, Any]] = None) -> None:
        """
        Always emits the same AIDL contract (as you specified) to ensure compatibility.
        Plan is currently not used to vary method signatures (unsafe); kept for traceability.
        """
        iv = """package android.hardware.automotive.vehicle;

import android.hardware.automotive.vehicle.VehiclePropValue;
import android.hardware.automotive.vehicle.IVehicleCallback;

interface IVehicle {
    VehiclePropValue get(int propId, int areaId);
    void set(in VehiclePropValue value);
    void registerCallback(in IVehicleCallback callback);
    void unregisterCallback(in IVehicleCallback callback);
}
"""
        cb = """package android.hardware.automotive.vehicle;

import android.hardware.automotive.vehicle.VehiclePropValue;

interface IVehicleCallback {
    void onPropertyEvent(in VehiclePropValue value);
}
"""
        vp = """package android.hardware.automotive.vehicle;

parcelable VehiclePropValue;
"""

        # Always write required files (sanitized paths; newline enforced)
        self.writer.write(f"{self.pkg_dir}/IVehicle.aidl", iv if iv.endswith("\n") else iv + "\n")
        self.writer.write(f"{self.pkg_dir}/IVehicleCallback.aidl", cb if cb.endswith("\n") else cb + "\n")
        self.writer.write(f"{self.pkg_dir}/VehiclePropValue.aidl", vp if vp.endswith("\n") else vp + "\n")

    # ---------------------------------------------------------------------
    # Utilities
    # ---------------------------------------------------------------------
    def _dump_raw(self, text: str, tag: str) -> None:
        (self.raw_dir / f"VHAL_AIDL_RAW_{tag}.txt").write_text(text or "", encoding="utf-8")

    def _sanitize(self, rel_path: str) -> Optional[str]:
        p = rel_path.replace("\\", "/").strip()
        p = re.sub(r"/+", "/", p)
        if p.startswith("/") or ".." in p.split("/"):
            return None
        if not p.startswith("hardware/interfaces/automotive/vehicle/aidl/"):
            return None
        return p


def generate_vhal_aidl(spec, plan: Optional[Dict[str, Any]] = None):
    """
    Backward compatible wrapper:
      - If caller doesn't pass plan, agent will attempt Phase-1 plan internally.
      - If caller passes plan (recommended in Option C), it will use it.
    """
    return VHALAidlAgent().run(spec.to_llm_spec(), plan=plan)
