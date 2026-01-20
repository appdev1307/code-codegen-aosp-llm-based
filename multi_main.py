# main.py - FINAL WORKING VERSION (with correct promotion)
from pathlib import Path
from vss_to_yaml import vss_to_yaml_spec
from schemas.yaml_loader import load_hal_spec_from_yaml_text
from agents.architect_agent import ArchitectAgent
from agents.module_planner_agent import plan_modules_from_spec
from agents.promote_draft_agent import PromoteDraftAgent
from tools.aosp_layout import ensure_aosp_layout


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

    print("🚀 Starting VSS → AAOS HAL Generation (Modular + LLM-Smart)")

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

    print("[2/4] Loading full HAL spec...")
    full_spec = load_hal_spec_from_yaml_text(yaml_spec)
    all_properties = full_spec.properties

    print("[3/4] Running Module Planner...")
    try:
        module_signal_map = plan_modules_from_spec(yaml_spec)
    except Exception as e:
        print(f"[FALLBACK] Module planner failed: {e}")
        ensure_aosp_layout(full_spec)
        ArchitectAgent().run(full_spec)
        return

    def get_property_id(prop):
        return getattr(prop, "property_id",
               getattr(prop, "prop_id",
               getattr(prop, "id",
               getattr(prop, "name", None))))

    prop_lookup = {get_property_id(p): p for p in all_properties if get_property_id(p)}
    print(f"[DEBUG] Property lookup: {len(prop_lookup)} entries")

    print(f"[4/4] Generating {len(module_signal_map)} modules...")
    architect = ArchitectAgent()
    ensure_aosp_layout(full_spec)

    for domain, signal_ids in module_signal_map.items():
        if not signal_ids:
            continue
        module_props = [prop_lookup.get(sid) for sid in signal_ids]
        module_props = [p for p in module_props if p]
        if not module_props:
            continue

        print(f"\n{'='*70}")
        print(f"GENERATING MODULE: {domain.upper()} ({len(module_props)} properties)")
        print(f"{'='*70}")

        module_spec = ModuleSpec(domain=domain, properties=module_props)
        try:
            architect.run(module_spec)
            print(f"✅ {domain.upper()} generated!")
        except Exception as e:
            print(f"❌ {domain.upper()}: {e}")

    print("\n🎉 All modules completed!")

    # === THIS IS THE KEY: PROMOTE DRAFTS TO FINAL PATH ===
    print("[PROMOTE] Promoting LLM drafts to final AOSP layout...")
    PromoteDraftAgent().run()

    # Generate shared SELinux policy once for the entire HAL
    print("[SELINUX] Generating shared vendor policy (combined)...")
    from agents.selinux_agent import generate_selinux
    generate_selinux(full_spec)  # Use the full original spec

    from agents.build_glue_agent import BuildGlueAgent
    BuildGlueAgent().run()

    print("\n🎉 SUCCESS! Final files are now in:")
    print("   → AIDL: output/hardware/interfaces/automotive/vehicle/aidl/android/hardware/automotive/vehicle/")
    print("   → C++:  output/hardware/interfaces/automotive/vehicle/impl/VehicleHalService.cpp")
    print("   → Plan: output/MODULE_PLAN.json")
    print("   → Drafts: output/.llm_draft/latest/ (for debugging)")


if __name__ == "__main__":
    main()