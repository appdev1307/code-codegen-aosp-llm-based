from llm_client import call_llm
from tools.safe_writer import SafeWriter


class VhalServiceAgent:
    def __init__(self):
        self.name = "VHAL Service Agent"
        self.output_dir = "output/vhal"
        self.writer = SafeWriter(self.output_dir)

    def build_prompt(self, spec_text: str) -> str:
        return f"""
You are an Android Automotive VHAL service developer.

Generate VHAL service implementation.

Rules:
- Follow AOSP AAOS architecture
- No explanations
- No placeholders

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
            raise RuntimeError("[LLM ERROR] Empty VHAL service output")

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


def generate_vhal_service(spec):
    return VhalServiceAgent().run(spec.to_llm_spec())
