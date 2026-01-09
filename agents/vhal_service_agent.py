import os
from llm_client import call_llm


class VHALServiceAgent:
    def __init__(self):
        self.name = "VHAL C++ Service Agent"
        self.output_dir = "output/vhal_service"

    def build_prompt(self, spec_text: str) -> str:
        return f"""
You are an AAOS Vehicle HAL C++ engineer.

Target:
- Android Automotive OS
- AIDL-based Vehicle HAL backend
- C++ NDK Binder
- Android 12+

Requirements:
- Implement BnIVehicle
- Implement get() and set()
- Implement property storage
- Implement callback notification
- Thread-safe
- Register service as:
  android.hardware.automotive.vehicle.IVehicle/default

No placeholders.
No explanations.

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

        os.makedirs(self.output_dir, exist_ok=True)
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


def generate_vhal_service(spec):
    return VHALServiceAgent().run(spec.to_llm_spec())
