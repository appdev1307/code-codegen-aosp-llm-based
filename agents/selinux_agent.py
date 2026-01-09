import os
from llm_client import call_llm


class SelinuxAgent:
    def __init__(self):
        self.name = "SELinux Agent"
        self.output_dir = "output/sepolicy"

    def build_prompt(self, spec_text: str) -> str:
        return f"""
You are an Android SELinux policy expert.

Generate SELinux rules for Vehicle HAL service.

Rules:
- Allow binder communication
- Define service domain
- Follow AOSP conventions
- No placeholders
- No explanations

IMPORTANT:
- All file paths MUST be RELATIVE
- DO NOT generate absolute paths like /etc, /system, /vendor
- Use AOSP-style relative paths only
  (e.g. sepolicy/private/vehicle_hal.te)

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
            raise RuntimeError("[LLM ERROR] Empty SELinux output")

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


def generate_selinux(spec):
    return SelinuxAgent().run(spec.to_llm_spec())
