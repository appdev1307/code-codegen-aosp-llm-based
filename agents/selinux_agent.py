from pathlib import Path
from llm_client import call_llm
from tools.safe_writer import SafeWriter


class SelinuxAgent:
    def __init__(self, output_root: str = "output"):
        self.name = "SELinux Agent"
        self.output_dir = str(Path(output_root) / "sepolicy")
        self.writer = SafeWriter(self.output_dir)

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
- Use AOSP-style paths only
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

        self._write_files(result)

        print(f"[DEBUG] {self.name}: done", flush=True)
        return result

    def _write_files(self, text: str):
        current = None
        buf = []

        for line in text.splitlines():
            if line.strip().startswith("--- FILE:"):
                if current:
                    self.writer.write(current, "\n".join(buf))
                current = (
                    line.replace("--- FILE:", "")
                    .replace("---", "")
                    .strip()
                )
                buf = []
            else:
                buf.append(line)

        if current:
            self.writer.write(current, "\n".join(buf))


def generate_selinux(spec, output_root: str = "output"):
    return SelinuxAgent(output_root=output_root).run(spec.to_llm_spec())
