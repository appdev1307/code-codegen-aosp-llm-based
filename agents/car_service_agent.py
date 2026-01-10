from llm_client import call_llm
from tools.safe_writer import SafeWriter


class CarServiceAgent:
    def __init__(self):
        self.name = "Car Service Agent"
        # ✅ Write into output/ so relative paths can be frameworks/...
        self.output_dir = "output"
        self.writer = SafeWriter(self.output_dir)

    def build_prompt(self, spec_text: str) -> str:
        return f"""
You are an Android Automotive Framework engineer.

Target:
- frameworks/base/services/core/java/com/android/server/car/
- Android 13+ (AAOS)
- Integrate with Vehicle HAL via CarPropertyService / CarPropertyManager patterns

Requirements:
- Generate framework-side service class for the given domain (HVAC -> CarHvacService)
- Subscribe to relevant properties and update internal state
- Provide handler thread usage (no blocking binder thread)
- No placeholders
- No explanations

Output format EXACTLY:
--- FILE: <relative path> ---
<file content>

ALL paths MUST be relative and under AOSP tree, e.g.:
frameworks/base/services/core/java/com/android/server/car/CarHvacService.java

Specification:
{spec_text}
""".lstrip()

    def run(self, spec_text: str) -> str:
        print(f"[DEBUG] {self.name}: start", flush=True)

        result = call_llm(self.build_prompt(spec_text))
        if not result.strip():
            raise RuntimeError("[LLM ERROR] Empty CarService output")

        self._write_files(result)

        print(f"[DEBUG] {self.name}: output -> {self.output_dir}", flush=True)
        print(f"[DEBUG] {self.name}: done", flush=True)
        return result

    def _write_files(self, text: str):
        current = None
        buf = []

        for line in text.splitlines():
            if line.strip().startswith("--- FILE:"):
                if current:
                    self.writer.write(current, "\n".join(buf).rstrip() + "\n")
                current = (
                    line.replace("--- FILE:", "")
                        .replace("---", "")
                        .strip()
                )
                buf = []
            else:
                buf.append(line)

        if current:
            self.writer.write(current, "\n".join(buf).rstrip() + "\n")


def generate_car_service(spec):
    return CarServiceAgent().run(spec.to_llm_spec())
