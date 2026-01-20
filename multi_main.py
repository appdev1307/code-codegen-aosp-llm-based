# main.py - Enhanced with LLM-powered modular generation (FINAL FIXED VERSION)
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
        self.properties = properties  # List of PropertySpec objects (not dicts!)

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
            # Safely extract values from PropertySpec object
            prop_id = getattr(prop, "property_id", 
                     getattr(prop, "prop_id", 
                     getattr(prop, "id", 
                     getattr(prop, "name", "UNKNOWN"))))
            typ = getattr(prop, "type", "UNKNOWN")
            access = getattr(prop, "access", "READ_WRITE")
            areas = getattr(prop, "areas", "GLOBAL")
            if isinstance(areas, list):
                areas = ", ".join(areas)
            elif not isinstance(areas, str):
                areas = "GLOBAL"

            lines += [
                f"- Property ID : {prop_id}",
                f"  Type : {typ}",
                f"  Access : {access}",
                f"  Areas : {areas}",
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
        max_props=50,  # Change to None for full run
        vendor_namespace="vendor.vss",
        add_meta=False,
    )
    spec_path = output_dir / "SPEC_FROM_VSS.yaml"
    spec_path.write_text(yaml_spec, encoding="utf-8")
    print(f"[DEBUG] Wrote {spec_path} with {n} properties")

    # === Step 2: Load full spec object ===
    print("[2/4] Loading full HAL spec...")
    full_spec = load_hal_spec_from_yaml_text(yaml_spec)
    all_properties = full_spec.properties  # List of PropertySpec objects

    # === Step 3: Let LLM intelligently group into modules ===
    print("[3/4] Running Module Planner (LLM analyzes full spec)...")
    try:
        module_signal_map = plan_modules_from_spec(yaml_spec)  # {"ADAS": ["VSS_VEHICLE_ADAS_...", ...], ...}
    except Exception as e:
        print(f"[WARN] Module planner failed: {e}")
        print("[FALLBACK] Running single full generation...")
        ensure_aosp_layout(full_spec)
        ArchitectAgent().run(full_spec)
        return

    # === Build safe lookup: Property ID string → PropertySpec object ===
    def get_property_id(prop):
        """Extract the string ID from a PropertySpec object using common attribute names"""
        return getattr(prop, "property_id",
               getattr(prop, "prop_id",
               getattr(prop, "id",
               getattr(prop, "name", None))))

    prop_lookup = {}
    for prop in all_properties:
        pid = get_property_id(prop)
        if pid and pid not in prop_lookup:
            prop_lookup[pid] = prop

    print(f"[DEBUG] Built property lookup with {len(prop_lookup)} entries")

    # === Step 4: Generate HAL per module ===
    print(f"[4/4] Generating HAL for {len(module_signal_map)} modules...")
    architect = ArchitectAgent()

    # Ensure base AOSP layout once (shared across modules)
    ensure_aosp_layout(full_spec)

    for domain, signal_ids in module_signal_map.items():
        if not signal_ids:
            print(f"  → Skipping empty module: {domain}")
            continue

        # Collect actual PropertySpec objects for this module
        module_props = []
        missing_count = 0
        for sid in signal_ids:
            if sid in prop_lookup:
                module_props.append(prop_lookup[sid])
            else:
                missing_count += 1

        if missing_count > 0:
            print(f"  → Warning: {missing_count} signal(s) not found in spec for module {domain}")

        if not module_props:
            print(f"  → No valid properties for {domain}, skipping")
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
    print("   → Check output/.llm_draft/latest/ for latest LLM drafts")
    print("   → Check output/hardware/interfaces/... for generated AOSP files")
    print("   → Check output/MODULE_PLAN.json for the LLM's smart grouping")


if __name__ == "__main__":
    main()