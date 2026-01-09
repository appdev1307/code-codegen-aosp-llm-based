import os
from llm_client import call_llm


class CarServiceAgent:
    def __init__(self):
        self.name = "Car Service Agent"
        self.output_dir = "output/car_service"

    def build_prompt(self, spec_text: str) -> str:
        return f"""
You are an Android Automotive Framework engineer.

Generate framework-side CarService integration for Vehicle HAL.

Rules:
- Use CarPropertyManager
- Reflect properties defined in HAL
- Java code
- No placeholders
- No explanations

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

        os.makedirs(self.output_dir, exist_ok=True)
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
                current = line.replace("--- FILE:", "").replace("---", "").strip()
                buf = []
            else:
                buf.append(line)

        if current:
            self._flush(current, buf)

    def _flush(self, rel, buf):
        path = os.path.join(self.output_dir, rel)
        os.makedirs(os.path.dirname(path), exist_ok=True)
        with open(path, "w") as f:
            f.write("\n".join(buf))


def generate_car_service(spec):
    return CarServiceAgent().run(spec.to_llm_spec())
