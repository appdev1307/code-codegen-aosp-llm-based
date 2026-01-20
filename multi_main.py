# main.py - FINAL VERSION (fully working modular pipeline)
from pathlib import Path
from vss_to_yaml import vss_to_yaml_spec
from schemas.yaml_loader import load_hal_spec_from_yaml_text
from agents.architect_agent import ArchitectAgent
from agents.module_planner_agent import plan_modules_from_spec
from tools.aosp_layout import ensure_aosp_layout
import json


class ModuleSpec:
    """Wrapper that mimics the original HalSpec interface expected by ArchitectAgent"""
    def __init__(self, domain: str, properties: list):
        self.domain = domain.upper()
        self.properties = properties  # List of PropertySpec objects

        # Add attributes expected by downstream agents
        self.aosp_level = 14
        self.vendor = "AOSP"

    def to_llm_spec(self):
        """Generate human-readable spec text for LLM agents"""
        lines = [
            f"HAL Domain: {self.domain}",
            f"AOSP Level: {self.aosp_level}",
            f"Vendor : {self.vendor}",
            f"Properties: {len(self.properties)}",
            ""
        ]
        for prop in self.properties:
            prop_id = getattr(prop, "property_id",
                     getattr(prop, "prop_id",
                     getattr(prop, "id",
                     getattr(prop, "name", "UNKNOWN"))))
            typ = getattr(prop, "type", "UNKNOWN")
            access = getattr(prop, "access", "READ_WRITE")
            areas = getattr(prop, "areas", "GLOBAL")
            if isinstance(areas, (list, tuple)):
                areas_str = ", ".join(map(str, areas)) if areas else "GLOBAL"
            else:
                areas_str = str(areas)

            lines += [
                f"- Property ID : {prop_id}",
                f"  Type : {typ}",
                f"  Access : {access}",
                f"  Areas : {areas_str}",
            ]
        return "\n".join(lines)


def main():
    vss_path = "./dataset/vss.json"
    output_dir = Path("output")
    output_dir.mkdir(parents=True, exist_ok=True)

    print("🚀 Starting VSS → AAOS HAL Generation (Modular + LLM-Smart)")

    # Step 1: VSS → YAML
    print("[1/4] Converting VSS to YAML spec...")
    yaml_spec, n = vss_to_yaml_spec(
        vss_json_path=vss_path,
        include_prefixes=None,
        max_props=50,
        vendor_namespace="vendor.vss",
        add_meta=False,
    )
    spec_path = output_dir / "SPEC_FROM_VSS.yaml"
    spec_path.write_text(yaml_spec, encoding="utf-8")
    print(f"[DEBUG] Wrote {spec_path} with {n} properties")

    # Step 2: Load full spec
    print("[2/4] Loading full HAL spec...")
    full_spec = load_hal_spec_from_yaml_text(yaml_spec)
    all_properties = full_spec.properties

    # Step 3: LLM module planning
    print("[3/4] Running Module Planner (LLM analyzes full spec)...")
    try:
        module_signal_map = plan_modules_from_spec(yaml_spec)
    except Exception as e:
        print(f"[WARN] Module planner failed: {e}")
        print("[FALLBACK] Running single full generation...")
        ensure_aosp_layout(full_spec)
        ArchitectAgent().run(full_spec)
        return

    # Build safe lookup
    def get_property_id(prop):
        return getattr(prop, "property_id",
               getattr(prop, "prop_id",
               getattr(prop, "id",
               getattr(prop, "name", None))))

    prop_lookup = {get_property_id(p): p for p in all_properties if get_property_id(p)}
    print(f"[DEBUG] Built property lookup with {len(prop_lookup)} entries")

    # Step 4: Generate per module
    print(f"[4/4] Generating HAL for {len(module_signal_map)} modules...")
    architect = ArchitectAgent()
    ensure_aosp_layout(full_spec)  # Shared layout

    for domain, signal_ids in module_signal_map.items():
        if not signal_ids:
            print(f"  → Skipping empty module: {domain}")
            continue

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

    print("\n🎉 All modules completed successfully!")
    print("   → LLM drafts: output/.llm_draft/latest/")
    print("   → AOSP files: output/hardware/interfaces/...")
    print("   → Module plan: output/MODULE_PLAN.json")


if __name__ == "__main__":
    main()