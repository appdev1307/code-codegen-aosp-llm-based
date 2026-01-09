import os
from llm_client import call_llm


class SelinuxAgent:
    def __init__(self):
        self.name = "SELinux Agent"
        self.output_dir = "output/sepolicy"

    def build_prompt(self, spec_text: str) -> str:
        return f"""
You are an Android SELinux policy expert.

Generate SELinux rules for an Android Automotive Vehicle HAL service.

Rules:
- Follow AOSP SELinux conventions
- Define service domain and type
- Allow required binder communication
- No placeholders
- No explanations

IMPORTANT:
- ALL file paths MUST be RELATIVE
- DO NOT generate absolute paths (/, /etc, /system, /vendor)
- Use AOSP-style relative paths only
  (e.g. sepolicy/private/vehicle_hal.te)

Output format EXACTLY:

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
        # 🔒 CRITICAL SAFETY GUARDS
        rel = rel.lstrip("/")              # prevent absolute paths
        if ".." in rel:
            raise RuntimeError(f"Invalid relative path from LLM: {rel}")

        path = os.path.join(self.output_dir, rel)
        os.makedirs(os.path.dirname(path), exist_ok=True)

        with open(path, "w") as f:
            f.write("\n".join(buf))


def generate_selinux(spec):
    return SelinuxAgent().run(spec.to_llm_spec())
