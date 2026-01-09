from schemas.hal_spec import HalSpec
from llm_client import call_llm
from pathlib import Path


def generate_domain(spec: HalSpec, out: Path):
    domain_dir = out / spec.domain.lower()
    domain_dir.mkdir(parents=True, exist_ok=True)

    prompt = f"""
Generate AAOS {spec.domain} domain HAL implementation.

Properties:
{spec.properties}

Generate C++ files:
- {spec.domain}Hal.h
- {spec.domain}Hal.cpp
"""

    text = call_llm(prompt)
    _write(text, domain_dir)


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
