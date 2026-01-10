from pathlib import Path
from llm_client import call_llm


class SpecYamlConverterAgent:
    def __init__(self, output_root: str = "output"):
        self.name = "Spec YAML Converter Agent"
        self.output_root = output_root
        Path(self.output_root).mkdir(parents=True, exist_ok=True)

    def build_prompt(self, simple_spec_text: str) -> str:
        return f"""
You are an automotive system specification normalizer.

ABSOLUTE OUTPUT RULES:
- Output YAML ONLY (no markdown, no fenced blocks)
- Do NOT output any backtick characters
- The first non-empty line MUST be: spec_version:
- Use 2-space indentation
- Ensure YAML parses with PyYAML

TASK:
Convert Simple Spec into YAML spec v1.1.

YAML REQUIRED STRUCTURE:
spec_version: 1.1
product:
  vendor: <string> (if missing -> "AOSP")
  android: <AAOS_13|AAOS_14|AAOS_15...> (if input says only "AAOS" -> "AAOS_14")
target:
  module: <HVAC|ADAS|MEDIA|POWER> (infer; default HVAC)
  domains: [vehicle_hal, car_service, sepolicy]
features: list of objects:
  - name: <snake_case>
    description: <string>
properties: list of objects

FOR EACH property in properties[] you MUST output:
- name, type (INT|FLOAT|BOOLEAN), access, areas
- aosp:
    standard: <true|false>
    kind: <system|vendor>
    id_hex: <0x...> (ONLY if standard=true and you know it; otherwise omit)
    defined_in: VehicleProperty.aidl (ONLY if standard=true)
    vendor_namespace: <vendor.module> (ONLY if standard=false)
- sdv:
    updatable_behavior: <true|false> (default true for controls, false for read-only sensors)
    cloud_control:
      allowed: <true|false>
      mode: <gated|disabled> (gated if allowed=true)
      requires: [vehicle_stationary, user_consent] (if allowed=true)
    telemetry:
      publish: <true|false> (default true)
      rate_limit_hz: <int> (default 1)

AOSP STANDARD HEURISTIC:
- HVAC_TEMPERATURE_SET is AOSP standard system property with id_hex 0x1140050A.
- If a property name is not known to you as AOSP standard, mark it as vendor.

Simple Spec:
{simple_spec_text}
""".lstrip()

    def run(self, simple_spec_text: str) -> str:
        print(f"[DEBUG] {self.name}: start", flush=True)

        yaml_text = call_llm(self.build_prompt(simple_spec_text))
        if not yaml_text or not yaml_text.strip():
            raise RuntimeError("[LLM ERROR] Empty YAML output from SpecYamlConverterAgent")

        out_path = Path(self.output_root) / "SPEC_NORMALIZED.yaml"
        out_path.write_text(yaml_text, encoding="utf-8")

        print(f"[DEBUG] {self.name}: wrote {out_path}", flush=True)
        print(f"[DEBUG] {self.name}: done", flush=True)
        return yaml_text
