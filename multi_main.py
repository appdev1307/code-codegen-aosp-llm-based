# main.py - Generate separate HAL module for each LLM-identified domain (N signals test)

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


# ────────────────────────────────────────────────
#  Configurable parameters
# ────────────────────────────────────────────────
TEST_SIGNAL_COUNT = 50                  # ← change here to test with different sizes
VSS_PATH          = "./dataset/vss.json"
VENDOR_NAMESPACE  = "vendor.vss"

# Persistent cache → only input-like files (limited + labelled)
# Survives when you delete/re-clone the whole project folder
PERSISTENT_CACHE_DIR = Path.home() / "vss_temp"
PERSISTENT_CACHE_DIR.mkdir(parents=True, exist_ok=True)

# All generated outputs (YAML spec, drafts, promoted files, docs, glue, selinux, app/backend ...)
# go here → inside the project → deleted when re-cloning repo
OUTPUT_DIR = Path("output")
# ────────────────────────────────────────────────


def main():
    OUTPUT_DIR.mkdir(parents=True, exist_ok=True)
    print(f"Starting VSS → AAOS HAL Generation ({TEST_SIGNAL_COUNT} signals test)")
    print(f"  Persistent cache (limited + labelled only): {PERSISTENT_CACHE_DIR}")
    print(f"  All generated files:                     {OUTPUT_DIR.resolve()}\n")

    # 1. Select first N signals
    print(f"[PREP] Selecting first {TEST_SIGNAL_COUNT} signals from {VSS_PATH}")
    try:
        with open(VSS_PATH, "r", encoding="utf-8") as f:
            full_vss = json.load(f)
    except Exception as e:
        print(f"Cannot read {VSS_PATH}: {e}")
        return

    if len(full_vss) < TEST_SIGNAL_COUNT:
        print(f"Warning: only {len(full_vss)} signals available")
        selected_signals = full_vss
    else:
        keys = sorted(full_vss.keys())
        selected_keys = keys[:TEST_SIGNAL_COUNT]
        selected_signals = {k: full_vss[k] for k in selected_keys}

    limited_path = PERSISTENT_CACHE_DIR / f"VSS_LIMITED_{TEST_SIGNAL_COUNT}.json"
    limited_path.write_text(json.dumps(selected_signals, indent=2, ensure_ascii=False))
    print(f"  → Wrote limited subset → {limited_path}")

    # 2. Label only the selected subset → saved outside project
    labelled_path = PERSISTENT_CACHE_DIR / f"VSS_LABELLED_{TEST_SIGNAL_COUNT}.json"

    if labelled_path.exists():
        print(f"[LABELLING] Using existing: {labelled_path}")
        with open(labelled_path, "r", encoding="utf-8") as f:
            labelled_data = json.load(f)
    else:
        print("[LABELLING] Labelling selected subset ...")
        labelling_agent = VSSLabellingAgent()
        labelled_data = labelling_agent.run(str(limited_path))
        labelled_path.write_text(json.dumps(labelled_data, indent=2, ensure_ascii=False))
        print(f"[LABELLING] Saved → {labelled_path}")

    # 3. Convert to YAML → saved inside project/output
    print("\n[YAML] Converting to HAL spec ...")
    yaml_spec, prop_count = vss_to_yaml_spec(
        vss_json_path=str(limited_path),
        include_prefixes=None,
        max_props=None,
        vendor_namespace=VENDOR_NAMESPACE,
        add_meta=True,
    )

    spec_path = OUTPUT_DIR / f"SPEC_FROM_VSS_{TEST_SIGNAL_COUNT}.yaml"
    spec_path.write_text(yaml_spec, encoding="utf-8")
    print(f"  → Wrote {spec_path} ({prop_count} properties)")

    # 4. Load spec
    full_spec = load_hal_spec_from_yaml_text(yaml_spec)
    all_properties = full_spec.properties

    # 5. Module planning
    print("[3/5] Running Module Planner...")
    try:
        module_signal_map = plan_modules_from_spec(yaml_spec)
        total = sum(len(v) for v in module_signal_map.values())
        print(f"  → {len(module_signal_map)} modules, {total} signals total")
    except Exception as e:
        print(f"[ERROR] Planner failed: {e}")
        return

    # 6. Generate modules → drafts go to project/.llm_draft/latest/
    print(f"[4/5] Generating {len(module_signal_map)} HAL modules...")
    architect = ArchitectAgent()

    prop_lookup = {p.property_id or p.prop_id or p.id or p.name: p 
                   for p in all_properties if p.property_id or p.prop_id or p.id or p.name}

    for domain, signal_ids in module_signal_map.items():
        if not signal_ids: continue
        module_props = [prop_lookup.get(sid) for sid in signal_ids if sid in prop_lookup]
        if not module_props: continue

        print(f"\n{'='*60}")
        print(f"  MODULE: {domain.upper()} ({len(module_props)} props)")
        print(f"{'='*60}")

        module_spec = ModuleSpec(domain=domain, properties=module_props)
        try:
            architect.run(module_spec)
            print(f"  → OK")
        except Exception as e:
            print(f"  → FAILED: {e}")

    print("\nAll HAL module drafts generated (inside project/.llm_draft/)")

    # 7. All other generated artifacts → also inside project / output
    print("[5/5] Generating supporting components...")
    DesignDocAgent().run(module_signal_map, all_properties, yaml_spec)           # → output/
    PromoteDraftAgent().run()                                                    # → promoted files in project
    generate_selinux(full_spec)                                                  # → SELinux files in project
    BuildGlueAgent().run()                                                       # → glue code in project
    LLMAndroidAppAgent().run(module_signal_map, all_properties)                  # → app drafts in project
    LLMBackendAgent().run(module_signal_map, all_properties)                     # → backend drafts in project

    print("\nFinished.")
    print(f"  → Labelled & limited files → {PERSISTENT_CACHE_DIR}")
    print(f"  → Design docs, app/backend drafts, .llm_draft/, promoted files, glue, selinux → {OUTPUT_DIR.resolve()}")


if __name__ == "__main__":
    main()