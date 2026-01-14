from pathlib import Path

from vss_to_yaml import vss_to_yaml_spec
from schemas.yaml_loader import load_hal_spec_from_yaml_text
from agents.architect_agent import ArchitectAgent
from tools.aosp_layout import ensure_aosp_layout


def main():
    vss_path = "./dataset/vss.json"

    # 1) Deterministic: VSS JSON -> YAML spec (NO LLM)
    yaml_spec, n = vss_to_yaml_spec(
        vss_json_path=vss_path,
        include_prefixes=None,
        max_props=200,                 # set None for all
        vendor_namespace="vendor.vss",
        add_meta=False,                # keep strict unless your loader supports meta
    )

    Path("output").mkdir(parents=True, exist_ok=True)
    Path("output/SPEC_FROM_VSS.yaml").write_text(yaml_spec, encoding="utf-8")
    print(f"[DEBUG] Wrote output/SPEC_FROM_VSS.yaml with {n} properties", flush=True)

    # 2) YAML -> HalSpec
    spec = load_hal_spec_from_yaml_text(yaml_spec)

    # 3) Existing pipeline: AOSP layout + LLM codegen agents (YES LLM here)
    ensure_aosp_layout(spec)
    ArchitectAgent().run(spec)


if __name__ == "__main__":
    main()
