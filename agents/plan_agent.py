# FILE: agents/plan_agent.py

import json
import re
from pathlib import Path
from typing import Any, Dict, Optional

from llm_client import call_llm
from schemas.hal_spec import HalSpec


class PlanAgent:
    """
    Option C (Two-Phase Generation) - Phase 1 Plan Agent (OEM-grade hardened)

    Goal:
    - LLM returns ONLY a small JSON plan (intent), not AOSP files.
    - Plan must NEVER block deterministic generation.
    - Robust to empty output, markdown fences, and extra text.

    Outputs a dict with stable keys:
      domain, aosp_level, vendor, callback_policy, default_change_mode, properties
    """

    def __init__(self):
        self.name = "HAL Plan Agent"
        self.output_root = "output"
        self.raw_dir = Path(self.output_root)
        self.raw_dir.mkdir(parents=True, exist_ok=True)

        # Keep consistent with other agents: strict JSON contract at the model boundary
        self.system = (
            "You are an Android Automotive HAL planning assistant.\n"
            "Output STRICT JSON only.\n"
            "No prose. No markdown. No code fences.\n"
            "If you cannot comply, output exactly: {\"plan\": {\"ok\": false, \"reason\": \"cannot_comply\"}}\n"
        )

    def build_prompt(self, spec: HalSpec) -> str:
        spec_text = spec.to_llm_spec()
        return f"""
Return ONLY a single JSON object. No markdown. No explanations.

Preferred schema:
{{
  "plan": {{
    "ok": true,
    "domain": "HVAC",
    "aosp_level": 14,
    "vendor": "AOSP",
    "callback_policy": "notify_on_change|notify_on_set",
    "default_change_mode": "ON_CHANGE|CONTINUOUS|STATIC",
    "properties": [
      {{
        "id": "VSS_...",
        "change_mode": "ON_CHANGE|CONTINUOUS|STATIC",
        "default": null
      }}
    ]
  }}
}}

Rules:
- Enums must match exactly (case-sensitive).
- Do not invent properties not in the spec.
- Keep the plan small: only include properties where you have a specific change_mode/default; otherwise omit or set default=null.
- If unsure: callback_policy="notify_on_change", default_change_mode="ON_CHANGE", property default=null.

Input spec (do not repeat):
{spec_text}
""".strip()

    def run(self, spec: HalSpec) -> Dict[str, Any]:
        print(f"[DEBUG] {self.name}: start", flush=True)

        prompt = self.build_prompt(spec)

        # Attempt 1
        out1 = call_llm(prompt, system=self.system, stream=False, temperature=0.0) or ""
        self._dump_raw(out1, 1)
        plan1 = self._parse_plan(out1)
        if plan1 is not None:
            plan = self._normalize_plan(plan1, spec)
            print(f"[DEBUG] {self.name}: done (properties in plan={len(plan.get('properties') or [])})", flush=True)
            return plan

        # Attempt 2 (repair)
        repair = (
            prompt
            + "\nREPAIR (MANDATORY):\n"
              "- Your previous output was INVALID.\n"
              "- Output ONLY JSON.\n"
              "- No markdown, no code fences, no explanations.\n"
              "- Must match the preferred schema.\n"
              "\nPREVIOUS OUTPUT (for correction, do not repeat):\n"
              f"{out1}\n"
        )
        out2 = call_llm(repair, system=self.system, stream=False, temperature=0.0) or ""
        self._dump_raw(out2, 2)
        plan2 = self._parse_plan(out2)
        if plan2 is not None:
            plan = self._normalize_plan(plan2, spec)
            print(f"[DEBUG] {self.name}: done (after repair) (properties in plan={len(plan.get('properties') or [])})", flush=True)
            return plan

        # Final safe default (must not block Option C)
        print(f"[WARN] {self.name}: invalid/empty plan from LLM; using safe defaults.", flush=True)
        return self._normalize_plan({}, spec)

    # ------------------------------------------------------------------
    # Parsing / Normalization
    # ------------------------------------------------------------------
    def _parse_plan(self, text: str) -> Optional[Dict[str, Any]]:
        """
        Accepts either:
          - preferred: {"plan": {...}}
          - legacy:    {"domain": ..., "aosp_level": ...}  (your older schema)
        Also tolerates code fences / extra text by extracting the first JSON object.
        """
        t = (text or "").strip()
        if not t:
            return None

        # Reject obvious markdown headings; tolerate fences by stripping
        if "\n###" in t:
            return None

        # Extract first JSON object if wrapped
        obj = self._extract_first_json_object(t)
        if not obj:
            return None

        try:
            data = json.loads(obj)
        except Exception:
            return None

        if not isinstance(data, dict):
            return None

        # Preferred envelope
        if "plan" in data and isinstance(data["plan"], dict):
            p = data["plan"]
            if p.get("ok") is False:
                return None
            return p

        # Legacy schema (backward compatibility)
        return data

    def _normalize_plan(self, plan: Dict[str, Any], spec: HalSpec) -> Dict[str, Any]:
        """
        Produce a stable plan dict that downstream agents can consume safely.
        Never throws; always returns a dict.
        """
        out: Dict[str, Any] = {}

        # domain
        domain = plan.get("domain") or getattr(spec, "domain", None) or "UNKNOWN"
        out["domain"] = str(domain).strip() or "UNKNOWN"

        # aosp_level
        aosp_level = plan.get("aosp_level") or getattr(spec, "aosp_level", None) or 14
        try:
            out["aosp_level"] = int(aosp_level)
        except Exception:
            out["aosp_level"] = 14

        # vendor
        vendor = plan.get("vendor") or getattr(spec, "vendor", None) or "AOSP"
        out["vendor"] = str(vendor).strip() or "AOSP"

        # callback_policy
        cb = plan.get("callback_policy") or "notify_on_change"
        cb = str(cb).strip()
        if cb not in ("notify_on_change", "notify_on_set"):
            cb = "notify_on_change"
        out["callback_policy"] = cb

        # default_change_mode
        dcm = plan.get("default_change_mode") or "ON_CHANGE"
        dcm = str(dcm).strip()
        if dcm not in ("ON_CHANGE", "CONTINUOUS", "STATIC"):
            dcm = "ON_CHANGE"
        out["default_change_mode"] = dcm

        # properties (optional; keep small)
        props = plan.get("properties")
        if not isinstance(props, list):
            props = []

        normalized_props = []
        for p in props:
            if not isinstance(p, dict):
                continue
            pid = p.get("id")
            if not isinstance(pid, str) or not pid.strip():
                continue
            change_mode = p.get("change_mode")
            if change_mode is not None:
                change_mode = str(change_mode).strip()
                if change_mode not in ("ON_CHANGE", "CONTINUOUS", "STATIC"):
                    change_mode = None
            default_val = p.get("default", None)

            normalized_props.append(
                {
                    "id": pid.strip(),
                    "change_mode": change_mode,
                    "default": default_val,
                }
            )

        out["properties"] = normalized_props
        return out

    def _extract_first_json_object(self, text: str) -> Optional[str]:
        """
        Extract the first balanced {...} JSON object from a possibly wrapped response.
        Handles common cases:
          - ```json ... ```
          - Leading 'Here is the JSON:'
        """
        s = text.lstrip()

        # Strip fences (common)
        s = re.sub(r"^\s*```(?:json)?\s*", "", s, flags=re.IGNORECASE)
        s = re.sub(r"\s*```\s*$", "", s)

        start = s.find("{")
        if start < 0:
            return None

        depth = 0
        for i in range(start, len(s)):
            ch = s[i]
            if ch == "{":
                depth += 1
            elif ch == "}":
                depth -= 1
                if depth == 0:
                    return s[start : i + 1]
        return None

    def _dump_raw(self, text: str, attempt: int) -> None:
        (self.raw_dir / f"PLAN_RAW_attempt{attempt}.txt").write_text(text or "", encoding="utf-8")
