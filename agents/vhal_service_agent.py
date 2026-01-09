from pathlib import Path

from llm_client import call_llm
from tools.safe_writer import SafeWriter


class VHALServiceAgent:
    def __init__(self):
        self.name = "VHAL C++ Service Agent"
        self.output_root = "output"              # ✅ unified root
        self.writer = SafeWriter(self.output_root)

    def build_prompt(self, spec_text: str) -> str:
        return f"""
YOU MUST OUTPUT ONLY FILE BLOCKS.
START YOUR RESPONSE WITH: --- FILE:

You are an AAOS Vehicle HAL C++ engineer.

Target:
- Android Automotive OS 14
- AIDL-based Vehicle HAL backend
- C++ NDK Binder

Requirements:
- Implement BnIVehicle
- Implement get() and set()
- Property storage
- Callback notification
- Thread-safe
- Register service:
  android.hardware.automotive.vehicle.IVehicle/default

Paths:
- Paths MUST start with: hardware/
- Example:
  hardware/interfaces/automotive/vehicle/impl/VehicleHalService.cpp

Output format EXACTLY:

--- FILE: <relative path> ---
<file content>

Specification:
{spec_text}
""".lstrip()

    def run(self, spec_text: str):
        print(f"[DEBUG] {self.name}: start", flush=True)

        result = call_llm(self.build_prompt(spec_text))
        if not result.strip():
            raise RuntimeError("[LLM ERROR] Empty VHAL service output")

        Path("output").mkdir(exist_ok=True)
        Path("output/VHAL_SERVICE_RAW.txt").write_text(result, encoding="utf-8")

        written = self._write_files(result)
        if written == 0:
            raise RuntimeError(
                "[FORMAT ERROR] No VHAL service files written. "
                "Expected --- FILE: blocks. See output/VHAL_SERVICE_RAW.txt"
            )

        print(f"[DEBUG] {self.name}: wrote {written} files", flush=True)
        return result

    def _write_files(self, text: str) -> int:
        current = None
        buf = []
        count = 0

        for line in text.splitlines():
            if line.strip().startswith("--- FILE:"):
                if current:
                    self.writer.write(current, "\n".join(buf).rstrip() + "\n")
                    count += 1
                current = line.replace("--- FILE:", "").replace("---", "").strip()
                buf = []
            else:
                buf.append(line)

        if current:
            self.writer.write(current, "\n".join(buf).rstrip() + "\n")
            count += 1

        return count


def generate_vhal_service(spec):
    return VHALServiceAgent().run(spec.to_llm_spec())
