from pathlib import Path
from typing import Optional

import yaml  # pip install pyyaml (you already use it elsewhere)
from llm_client import call_llm


class SpecYamlConverterAgent:
    def __init__(self, output_root: str = "output", max_retries: int = 2):
        self.name = "Spec YAML Converter Agent"
        self.output_root = output_root
        self.max_retries = max_retries
        Path(self.output_root).mkdir(parents=True, exist_ok=True)

    def build_prompt(self, simple_spec_text: str) -> str:
        return f"""
You are an automotive system specification normalizer.

ABSOLUTE OUTPUT RULES:
- Output VALID YAML ONLY. No markdown. No prose. No questions.
- The first non-empty line MUST be exactly: spec_version: 1.1
- Use 2-space indentation.
- Must parse with PyYAML.
- Must include top-level keys: spec_version, product, target, features, properties
- properties MUST be a YAML list (even if empty).

TASK:
Convert the input "Simple Spec" into YAML spec v1.1.

DEFAULTS (when missing in input):
- product.vendor: "AOSP"
- product.android: "AAOS_14" (if input says only "AAOS")
- target.module: infer from content; default "HVAC"
- target.domains: [vehicle_hal, car_service, sepolicy]
- features: if not given, create one feature: {{name: generated_from_vss, description: Generated from VSS}}
- For each property:
  - name: required (string)
  - type: one of [INT, FLOAT, BOOLEAN, STRING]
  - access: one of [READ, WRITE, READ_WRITE]
  - areas: required; if missing -> [GLOBAL]

AOSP / VENDOR RULES:
- If the property is NOT a known AOSP standard property, mark it vendor.
- Only set id_hex/defined_in if you are confident. Otherwise omit them.

REQUIRED YAML STRUCTURE:
spec_version: 1.1
product:
  vendor: <string>
  android: <string>
target:
  module: <HVAC|ADAS|MEDIA|POWER>
  domains: [vehicle_hal, car_service, sepolicy]
features:
  - name: <snake_case>
    description: <string>
properties:
  - name: <string>
    type: <INT|FLOAT|BOOLEAN|STRING>
    access: <READ|WRITE|READ_WRITE>
    areas: [<string>, ...]
    aosp:
      standard: <true|false>
      kind: <system|vendor>
      vendor_namespace: <string>   # required when standard=false, else omit
    sdv:
      updatable_behavior: <true|false>
      cloud_control:
        allowed: <true|false>
        mode: <gated|disabled>
      telemetry:
        publish: <true|false>
        rate_limit_hz: <int>

HEURISTIC EXAMPLES (only when applicable):
- HVAC_TEMPERATURE_SET is AOSP standard system property with id_hex 0x1140050A.

Simple Spec:
{simple_spec_text}
""".lstrip()

    def _validate_yaml(self, yaml_text: str) -> Optional[str]:
        """
        Returns None if OK, else returns error message.
        """
        if not yaml_text or not yaml_text.strip():
            return "Empty YAML"

        low = yaml_text.strip().lower()
        if "i'm sorry" in low or "could you please provide" in low or "missing some context" in low:
            return "Model returned non-YAML apology/clarification text"

        try:
            obj = yaml.safe_load(yaml_text)
        except Exception as e:
            return f"PyYAML parse error: {e}"

        if not isinstance(obj, dict):
            return "Top-level YAML is not a mapping/object"

        required = ["spec_version", "product", "target", "features", "properties"]
        missing = [k for k in required if k not in obj]
        if missing:
            return f"Missing required top-level keys: {missing}"

        if obj.get("spec_version") != 1.1 and str(obj.get("spec_version")) != "1.1":
            return f"spec_version is not 1.1: got {obj.get('spec_version')}"

        if not isinstance(obj.get("properties"), list):
            return "properties is not a YAML list"

        return None

    def run(self, simple_spec_text: str) -> str:
        print(f"[DEBUG] {self.name}: start", flush=True)

        prompt = self.build_prompt(simple_spec_text)

        last_yaml = ""
        last_err = ""

        # attempt 1 + retries
        for attempt in range(1, self.max_retries + 2):
            yaml_text = call_llm(prompt)
            last_yaml = yaml_text or ""

            # dump each attempt for debugging
            dump_path = Path(self.output_root) / f"SPEC_NORMALIZED_attempt_{attempt}.yaml"
            dump_path.write_text(last_yaml, encoding="utf-8")

            err = self._validate_yaml(last_yaml)
            if not err:
                out_path = Path(self.output_root) / "SPEC_NORMALIZED.yaml"
                out_path.write_text(last_yaml, encoding="utf-8")
                print(f"[DEBUG] {self.name}: wrote {out_path}", flush=True)
                print(f"[DEBUG] {self.name}: done", flush=True)
                return last_yaml

            last_err = err
            print(f"[WARN] {self.name}: attempt {attempt} invalid YAML ({err}). Retrying...", flush=True)

        # If all attempts failed
        preview = "\n".join(last_yaml.splitlines()[:30])
        raise RuntimeError(
            f"[LLM ERROR] SpecYamlConverterAgent failed after {self.max_retries + 1} attempts.\n"
            f"Last error: {last_err}\n"
            f"Preview:\n{preview}\n"
            f"See dumped attempts in: {self.output_root}/SPEC_NORMALIZED_attempt_*.yaml"
        )
