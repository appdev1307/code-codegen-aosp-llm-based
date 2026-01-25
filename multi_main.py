# main.py - 50-Signal Test with LLM Labelling & Multi-Module HAL Generation
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
from agents.vss_labelling_agent import VSSLabellingAgent
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

    print("🚀 Starting VSS → AAOS HAL Generation (50 signals, one module per domain)")

    # === Step 1: LLM Labelling (run once) ===
    labelled_path = output_dir / "VSS_LABELLED.json"
    if not labelled_path.exists():
        print("[LABELLING] Running LLM-assisted labelling (first time only)...")
        labelling_agent = VSSLabellingAgent()
        labelled_data = labelling_agent.run(vss_path)
    else:
        print(f"[LABELLING] Loading existing labelled dataset: {labelled_path}")
        with open(labelled_path, "r", encoding="utf-8") as f:
            labelled_data = json.load(f)

    # === Step 2: Limit to 50 signals ===
    print("\n[1/5] Preparing 50-signal test dataset...")
    limited_signals = dict(list(labelled_data.items())[:50])
    print(f"Selected {len(limited_signals)} signals for testing")

    limited_path = output_dir / "VSS_50_SIGNALS.json"
    limited_path.write_text(json.dumps(limited_signals, indent=2, ensure_ascii=False))

    # === Step 3: Convert to YAML spec ===
    print("[2/5] Converting to YAML spec...")
    yaml_spec, n = vss_to_yaml_spec(
        vss_json_path=str(limited_path),
        include_prefixes=None,
        max_props=None,
        vendor_namespace="vendor.vss",
        add_meta=True,
    )

    spec_path = output_dir / "SPEC_FROM_VSS.yaml"
    spec_path.write_text(yaml_spec, encoding="utf-8")
    print(f"[DEBUG] Wrote {spec_path} with {n} properties")

    # === Step 4: Load spec ===
    print("[3/5] Loading HAL spec...")
    full_spec = load_hal_spec_from_yaml_text(yaml_spec)
    all_properties = full_spec.properties

    # === Step 5: LLM Module Planning ===
    print("[4/5] Running Module Planner...")
    try:
        module_signal_map = plan_modules_from_spec(yaml_spec)
        print(f"LLM identified {len(module_signal_map)} modules: {', '.join(module_signal_map.keys())}")
    except Exception as e:
        print(f"[FALLBACK] Module planner failed: {e}")
        ensure_aosp_layout(full_spec)
        ArchitectAgent().run(full_spec)
        return

    # === Property lookup ===
    def get_property_id(prop):
        return getattr(prop, "property_id",
               getattr(prop, "prop_id",
               getattr(prop, "id",
               getattr(prop, "name", None))))

    prop_lookup = {get_property_id(p): p for p in all_properties if get_property_id(p)}

    # === Generate ONE HAL MODULE PER DOMAIN ===
    print(f"[5/5] Generating {len(module_signal_map)} separate HAL modules...")
    architect = ArchitectAgent()
    ensure_aosp_layout(full_spec)  # Shared base layout

    for domain, signal_ids in module_signal_map.items():
        if not signal_ids:
            continue

        module_props = [prop_lookup.get(sid) for sid in signal_ids if prop_lookup.get(sid)]
        module_props = [p for p in module_props if p]

        if not module_props:
            continue

        print(f"\n{'='*80}")
        print(f"GENERATING MODULE: {domain.upper()} ({len(module_props)} properties)")
        print(f"{'='*80}")

        module_spec = ModuleSpec(domain=domain, properties=module_props)
        try:
            architect.run(module_spec)
            print(f"✅ {domain.upper()} module generated successfully!")
        except Exception as e:
            print(f"❌ {domain.upper()} generation failed: {e}")

    print("\n🎉 All HAL modules completed!")

    # === Final Supporting Components ===
    print("\nGenerating full-stack supporting components...")

    print("  → Design documents & UML...")
    DesignDocAgent().run(module_signal_map, all_properties, yaml_spec)

    print("  → Promoting drafts to final layout...")
    PromoteDraftAgent().run()  # Will merge all (if you applied merge fix)

    print("  → Shared SELinux policy...")
    generate_selinux(full_spec)

    print("  → AOSP build glue...")
    BuildGlueAgent().run()

    print("  → Dynamic Android Car App...")
    LLMAndroidAppAgent().run(module_signal_map, all_properties)

    print("  → Telemetry backend...")
    LLMBackendAgent().run(module_signal_map, all_properties)

    print("\n🎉 SUCCESS! 50-signal multi-module run complete!")
    print("    → Modules: ADAS, HVAC, BODY, etc.")
    print("    → Full HAL, App, Backend, Design Docs generated")
    print("    → Ready to scale: remove [:50] limit for full dataset")


if __name__ == "__main__":
    main()