# main.py - FINAL VERSION (50 signals test + optional labelling)
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

    print("🚀 Starting VSS → AAOS HAL Generation (Testing with 50 signals)")

    # === Optional: LLM Labelling (run once, then comment out) ===
    labelled_path = output_dir / "VSS_LABELLED.json"
    if not labelled_path.exists():
        print("[LABELLING] Running LLM-assisted labelling (first-time only)...")
        labelling_agent = VSSLabellingAgent()
        labelled_data = labelling_agent.run(vss_path)
        # Save for future runs
        labelled_path.write_text(json.dumps(labelled_data, indent=2, ensure_ascii=False))
        print(f"[LABELLING] Saved labelled dataset to {labelled_path}")
    else:
        print(f"[LABELLING] Using existing labelled dataset: {labelled_path}")
        with open(labelled_path, "r", encoding="utf-8") as f:
            labelled_data = json.load(f)

    # === Limit to 50 signals for testing ===
    print("\n[1/5] Preparing 50-signal dataset...")
    limited_signals = dict(list(labelled_data.items())[:50])  # ← THIS WAS MISSING!
    print(f"Selected {len(limited_signals)} signals for test run")

    limited_path = output_dir / "VSS_LIMITED_50.json"
    limited_path.write_text(json.dumps(limited_signals, indent=2, ensure_ascii=False))

    # === Convert to YAML ===
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

    # === Load and proceed ===
    print("[2/5] Loading full HAL spec...")
    full_spec = load_hal_spec_from_yaml_text(yaml_spec)
    all_properties = full_spec.properties

    print("[3/5] Running Module Planner...")
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

    print(f"[4/5] Generating {len(module_signal_map)} modules...")
    architect = ArchitectAgent()
    ensure_aosp_layout(full_spec)

    for domain, signal_ids in module_signal_map.items():
        if not signal_ids:
            continue
        module_props = [prop_lookup.get(sid) for sid in signal_ids]
        module_props = [p for p in module_props if p]
        if not module_props:
            continue

        print(f"\n{'='*80}")
        print(f"GENERATING MODULE: {domain.upper()} ({len(module_props)} properties)")
        print(f"{'='*80}")

        module_spec = ModuleSpec(domain=domain, properties=module_props)
        try:
            architect.run(module_spec)
            print(f"✅ {domain.upper()} generated!")
        except Exception as e:
            print(f"❌ {domain.upper()}: {e}")

    print("\n🎉 All HAL modules completed!")

    # === Full-Stack Generation ===
    print("[5/5] Generating full-stack components...")
    print("  → Generating design documents...")
    DesignDocAgent().run(module_signal_map, all_properties, yaml_spec)
    print("  → Promoting drafts to final layout...")
    PromoteDraftAgent().run()
    print("  → Generating SELinux policy...")
    generate_selinux(full_spec)
    print("  → Generating build glue...")
    BuildGlueAgent().run()
    print("  → Generating Android app...")
    LLMAndroidAppAgent().run(module_signal_map, all_properties)
    print("  → Generating backend...")
    LLMBackendAgent().run(module_signal_map, all_properties)

    print("\n🎉 SUCCESS! Test run with 50 signals complete!")
    print("    → Ready to scale: change [:50] to full labelled_data")
    print("    → All files in output/")


if __name__ == "__main__":
    main()