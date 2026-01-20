# main.py - Enhanced with LLM-powered modular generation
from pathlib import Path
from vss_to_yaml import vss_to_yaml_spec
from schemas.yaml_loader import load_hal_spec_from_yaml_text
from agents.architect_agent import ArchitectAgent
from agents.module_planner_agent import plan_modules_from_spec  # New!
from tools.aosp_layout import ensure_aosp_layout
import json


class ModuleSpec:
    """Wrapper to feed module-specific data to ArchitectAgent"""
    def __init__(self, domain: str, properties: list):
        self.domain = domain.upper()
        self.properties = properties  # List of full property dicts

    def to_llm_spec(self):
        """Generate human-readable spec text for LLM agents (same format as original)"""
        lines = [
            f"HAL Domain: {self.domain}",
            f"AOSP Level: 14",
            f"Vendor : AOSP",
            f"Properties: {len(self.properties)}",
            ""
        ]
        for prop in self.properties:
            lines += [
                f"- Property ID : {prop.get('Property ID', 'UNKNOWN')}",
                f"  Type : {prop.get('Type', 'UNKNOWN')}",
                f"  Access : {prop.get('Access', 'READ_WRITE')}",
                f"  Areas : {prop.get('Areas', 'GLOBAL')}",
            ]
        return "\n".join(lines)


def main():
    vss_path = "./dataset/vss.json"
    output_dir = Path("output")
    output_dir.mkdir(parents=True, exist_ok=True)

    print("🚀 Starting VSS → AAOS HAL Generation (Modular + LLM-Smart)")

    # === Step 1: Convert VSS JSON → YAML spec (deterministic, no LLM) ===
    print("[1/4] Converting VSS to YAML spec...")
    yaml_spec, n = vss_to_yaml_spec(
        vss_json_path=vss_path,
        include_prefixes=None,
        max_props=50,  # Remove or set to None for full dataset
        vendor_namespace="vendor.vss",
        add_meta=False,
    )
    spec_path = output_dir / "SPEC_FROM_VSS.yaml"
    spec_path.write_text(yaml_spec, encoding="utf-8")
    print(f"[DEBUG] Wrote {spec_path} with {n} properties")

    # === Step 2: Load full spec object ===
    print("[2/4] Loading full HAL spec...")
    full_spec = load_hal_spec_from_yaml_text(yaml_spec)
    all_properties = full_spec.properties  # Assuming your HalSpec has .properties list

    # === Step 3: Let LLM intelligently group into modules ===
    print("[3/4] Running Module Planner (LLM analyzes full spec)...")
    try:
        module_signal_map = plan_modules_from_spec(yaml_spec)  # Returns {"HVAC": [ids...], "BODY": ...}
    except Exception as e:
        print(f"[WARN] Module planner failed: {e}")
        print("[FALLBACK] Using single full run (may trigger LLM fallbacks)")
        ensure_aosp_layout(full_spec)
        ArchitectAgent().run(full_spec)
        return

    # Build lookup: Property ID → full property dict
    prop_lookup = {prop["Property ID"]: prop for prop in all_properties}

    # === Step 4: Generate HAL per module ===
    print(f"[4/4] Generating HAL for {len(module_signal_map)} modules...")
    architect = ArchitectAgent()

    # Ensure base AOSP layout once
    ensure_aosp_layout(full_spec)

    for domain, signal_ids in module_signal_map.items():
        if not signal_ids:
            print(f"  → Skipping empty module: {domain}")
            continue

        # Filter properties for this module
        module_props = [prop_lookup[sid] for sid in signal_ids if sid in prop_lookup]
        if not module_props:
            print(f"  → No properties found for {domain}, skipping")
            continue

        print(f"\n{'='*70}")
        print(f"GENERATING MODULE: {domain.upper()} ({len(module_props)} properties)")
        print(f"{'='*70}")

        module_spec = ModuleSpec(domain=domain, properties=module_props)

        try:
            architect.run(module_spec)
            print(f"✅ {domain.upper()} module generated successfully!")
        except Exception as e:
            print(f"❌ Error generating {domain.upper()}: {e}")

    print("\n🎉 All modules completed!")
    print("   → Check output/.llm_draft/latest/ for LLM drafts")
    print("   → Check output/hardware/interfaces/... for final AOSP files")
    print("   → Check output/MODULE_PLAN.json for the smart grouping")


if __name__ == "__main__":
    main()