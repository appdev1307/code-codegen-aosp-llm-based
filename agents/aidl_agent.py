from llm_client import call_llm
from schemas.hal_spec import HalSpec
from pathlib import Path


def generate_aidl(spec: HalSpec, out: Path):
    prompt = f"""
Generate AOSP AAOS Vehicle HAL AIDL.

Package: android.hardware.automotive.vehicle
Domain: {spec.domain}
AOSP Level: {spec.aosp_level}

Properties:
{[p.id for p in spec.properties]}

Generate:
- IVehicle.aidl
- IVehicleCallback.aidl
- VehiclePropValue.aidl
"""

    text = call_llm(prompt)
    write_llm_files(text, out)


def write_llm_files(text: str, base: Path):
    current = None
    buf = []
    for line in text.splitlines():
        if line.startswith("--- FILE:"):
            if current:
                _flush(current, buf, base)
            current = line.replace("--- FILE:", "").strip()
            buf = []
        else:
            buf.append(line)
    if current:
        _flush(current, buf, base)


def _flush(rel, buf, base):
    path = base / rel
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("\n".join(buf))
