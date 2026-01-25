# main.py - 50-SIGNAL TEST ONLY (No labelling, fast run, same 32B model) - FIXED
from pathlib import Path
import json

from vss_to_yaml import vss_to_yaml_spec
from schemas.yaml_loader import load_hal_spec_from_yaml_text
from agents.architect_agent import ArchitectAgent
from agents.module_planner_agent import plan_modules_from_spec
from agents.promote_draft_agent import PromoteDraftAgent
from agents.design_doc_agent import DesignDocAgent
from agents.selinux_agent import generate_selinux
from agents.build_glue_agent import BuildGlueAgent
from agents.llm_android_app_agent import LLMAndroidAppAgent
from agents.llm_backend_agent import LLMBackendAgent
from tools.aosp_layout import ensure_aosp_layout


# Helper to flatten VSS (same as in labelling agent)
def flatten_vss(vss_data, current_path=""):
    flat = {}
    for key, value in vss_data.items():
        full_path = f"{current_path}.{key}" if current_path else key
        if "datatype" in value and value.get("type") != "branch":
            flat[full_path] = value
        elif isinstance(value, dict):
            flat.update(flatten_vss(value, full_path))
    return flat


class ModuleSpec:
    def __init__(self, domain: str, properties: list):
        self.domain = domain.upper()
        self.properties = properties
        self.aosp_level = 14
        self.vendor = "AOSP"

    def to_llm_spec(self):
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
            areas_str = ", ".join(areas) if isinstance(areas, (list, tuple)) and areas else str(areas)

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

    print("🚀 Starting VSS → AAOS HAL Generation (50 signals test — fast mode)")

    # Load and flatten raw VSS
    with open(vss_path, "r", encoding="utf-8") as f:
        raw_vss = json.load(f)

    leaf_signals = flatten_vss(raw_vss)
    print(f"[DATA] Found {len(leaf_signals)} total leaf signals")

    # Limit to first 50
    limited_signals = dict(list(leaf_signals.items())[:50])
    print(f"[TEST] Using only 50 signals for fast testing")

    # === FIX: Save to temp file to ensure vss_to_yaml_spec processes all 50 ===
    temp_vss_path = output_dir / "TEMP_50_SIGNALS.json"
    temp_vss_path.write_text(json.dumps(limited_signals, indent=2, ensure_ascii=False))
    print(f"[DEBUG] Saved 50 signals to {temp_vss_path}")

    # Convert to YAML using file path (reliable)
    yaml_spec, n = vss_to_yaml_spec(
        vss_json_path=str(temp_vss_path),
        include_prefixes=None,
        max_props=None,
        vendor_namespace="vendor.vss",
        add_meta=False,
    )

    spec_path = output_dir / "SPEC_FROM_VSS.yaml"
    spec_path.write_text(yaml_spec, encoding="utf-8")
    print(f"[DEBUG] Wrote {spec_path} with {n} properties")

    # Load spec
    full_spec = load_hal_spec_from_yaml_text(yaml_spec)
    all_properties = full_spec.properties

    # Module planning
    print("\n[MODULE PLANNER] Running...")
    module_signal_map = plan_modules_from_spec(yaml_spec)
    print(f"LLM identified {len(module_signal_map)} modules")

    # Property lookup
    def get_property_id(prop):
        return getattr(prop, "property_id",
               getattr(prop, "prop_id",
               getattr(prop, "id",
               getattr(prop, "name", None))))

    prop_lookup = {get_property_id(p): p for p in all_properties if get_property_id(p)}

    # Generate modules
    print(f"\n[GENERATION] Generating {len(module_signal_map)} HAL modules...")
    architect = ArchitectAgent()
    ensure_aosp_layout(full_spec)

    for domain, signal_ids in module_signal_map.items():
        if not signal_ids:
            continue
        module_props = [prop_lookup.get(sid) for sid in signal_ids if prop_lookup.get(sid)]
        if not module_props:
            continue

        print(f"\nGENERATING MODULE: {domain.upper()} ({len(module_props)} properties)")
        module_spec = ModuleSpec(domain=domain, properties=module_props)
        try:
            architect.run(module_spec)
            print(f"✅ {domain.upper()} generated!")
        except Exception as e:
            print(f"❌ {domain.upper()}: {e}")

    print("\n🎉 HAL generation complete!")

    # Final stack
    print("\nGenerating supporting components...")
    DesignDocAgent().run(module_signal_map, all_properties, yaml_spec)
    PromoteDraftAgent().run()
    generate_selinux(full_spec)
    BuildGlueAgent().run()
    LLMAndroidAppAgent().run(module_signal_map, all_properties)
    LLMBackendAgent().run(module_signal_map, all_properties)

    print("\n🎉 50-signal test run complete!")
    print("    → All 50 signals preserved and used")
    print("    → Ready for full run: remove temp file + [:50]")


if __name__ == "__main__":
    main()