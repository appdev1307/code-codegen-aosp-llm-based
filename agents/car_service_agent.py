from llm_client import call_llm
from tools.safe_writer import SafeWriter


class CarServiceAgent:
    def __init__(self):
        self.name = "Car Service Agent"
        self.output_dir = "output/car_service"
        self.writer = SafeWriter(self.output_dir)

    def build_prompt(self, spec_text: str) -> str:
        return f"""
You are an Android Automotive Framework engineer.

Generate framework-side CarService integration for Vehicle HAL.

Rules:
- Use CarPropertyManager
- Reflect properties defined in HAL
- Java code only
- Follow AOSP framework conventions
- No placeholders
- No explanations

IMPORTANT:
- All file paths MUST be RELATIVE
- DO NOT generate absolute paths
- Use AOSP-style paths
  (e.g. frameworks/base/services/car/... )

Output format:
--- FILE: <relative path> ---
<file content>

Specification:
{spec_text}
"""

    def run(self, spec_text: str):
        print(f"[DEBUG] {self.name}: start", flush=True)

        result = call_llm(self.build_prompt(spec_text))
        if not result.strip():
            raise RuntimeError("[LLM ERROR] Empty CarService output")

        self._write_files(result)

        print(f"[DEBUG] {self.name}: done", flush=True)
        return result

    def _write_files(self, text: str):
        current = None
        buf = []

        for line in text.splitlines():
            if line.strip().startswith("--- FILE:"):
                if current:
                    self._flush(current, buf)
                current = (
                    line.replace("--- FILE:", "")
                    .replace("---", "")
                    .strip()
                )
                buf = []
            else:
                buf.append(line)

        if current:
            self._flush(current, buf)

    def _flush(self, rel, buf):
        self.writer.write(rel, "\n".join(buf))


def generate_car_service(spec):
    return CarServiceAgent().run(spec.to_llm_spec())
