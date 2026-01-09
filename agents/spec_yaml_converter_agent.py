from pathlib import Path
from llm_client import call_llm


class SpecYamlConverterAgent:
    """
    Convert designer simple spec (free text) into standardized YAML-only spec.
    YAML is later converted to HalSpec/PropertySpec (with normalization).
    """

    def __init__(self, output_root: str = "output"):
        self.name = "Spec YAML Converter Agent"
        self.output_root = output_root
        Path(self.output_root).mkdir(parents=True, exist_ok=True)

    def build_prompt(self, simple_spec_text: str) -> str:
        return f"""
You are an automotive system specification normalizer.

TASK:
Convert the provided Simple Spec into STRICT YAML ONLY.

YAML SCHEMA (v1.1):
spec_version: 1.1
product:
  vendor: <string> (if missing -> "AOSP")
  android: <AAOS_13|AAOS_14|AAOS_15...> (if input says only "AAOS" -> use "AAOS_14")
target:
  module: <HVAC|ADAS|MEDIA|POWER> (MUST be one of these; infer from text; default HVAC)
  domains: [vehicle_hal, car_service, sepolicy] (default)
features: (optional list of strings)
properties: list of properties

Each properties[] item MUST include:
- name: <string> (use Property ID string as name)
- type: INT|FLOAT|BOOLEAN (MUST be one of these; map INT32/INT64/INTEGER -> INT)
- access: READ|WRITE|READ_WRITE (MUST be one of these)
- areas: YAML list of strings (if GLOBAL or not provided -> [])

If a Range is provided like:
  Range: 0..3 step 1
then include:
constraints:
  min: 0
  max: 3
  step: 1

OUTPUT RULES (MANDATORY):
- Output YAML ONLY
- No markdown, no ``` fences, no explanation
- Use 2-space indentation
- Ensure valid YAML parsable by PyYAML

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
