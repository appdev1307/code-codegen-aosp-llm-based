from schemas.hal_spec import HalSpec
from llm_client import call_llm
from pathlib import Path


def generate_service(spec: HalSpec, out: Path):
    prompt = f"""
Generate AAOS Vehicle HAL service layer.

Requirements:
- BnIVehicle
- Route calls to {spec.domain}Hal
- Thread-safe
- Binder-ready

Generate:
- VehicleHal.h
- VehicleHal.cpp
- main.cpp
"""

    text = call_llm(prompt)
    _write(text, out)


def _write(text, base):
    current, buf = None, []
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
