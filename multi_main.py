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
# Configurable parameters
# ────────────────────────────────────────────────
TEST_SIGNAL_COUNT = 50                  # ← change here to test with different sizes
VSS_PATH          = "./dataset/vss.json"
VENDOR_NAMESPACE  = "vendor.vss"

# Persistent cache folder — only for input-like files (limited subset + labels)
PERSISTENT_CACHE_DIR = Path.home() / "vss_temp"  # recommended for Colab & local
PERSISTENT_CACHE_DIR.mkdir(parents=True, exist_ok=True)

# All generated outputs stay inside project
OUTPUT_DIR = Path("output")
# ────────────────────────────────────────────────


class ModuleSpec:
    def __init__(self, domain: str, properties: list):
        self.domain = domain.upper()
        self.properties = properties
        self.aosp_level = 14
        self.vendor = "AOSP"


def main():
    OUTPUT_DIR.mkdir(parents=True, exist_ok=True)
    print(f"Starting VSS → AAOS HAL Generation ({TEST_SIGNAL_COUNT} signals test)")
    print(f"  Persistent cache: {PERSISTENT_CACHE_DIR}")
    print(f"  Project output  : {OUTPUT_DIR.resolve()}\n")

    # 1. Select first N signals
    print(f"[PREP] Selecting first {TEST_SIGNAL_COUNT} signals from {VSS_PATH}")
    try:
        with open(VSS_PATH, "r", encoding="utf-8") as f:
            full_vss = json.load(f)
    except Exception as e:
        print(f"Cannot read {VSS_PATH}: {e}")
        return

    if len(full_vss) < TEST_SIGNAL_COUNT:
        print(f"Warning: only {len(full_vss)} signals available (requested {TEST_SIGNAL_COUNT})")
        selected_signals = full_vss
    else:
        keys = sorted(full_vss.keys())
        selected_keys = keys[:TEST_SIGNAL_COUNT]
        selected_signals = {k: full_vss[k] for k in selected_keys}

    print(f"Selected {len(selected_signals)} signals")

    limited_path = PERSISTENT_CACHE_DIR / f"VSS_LIMITED_{TEST_SIGNAL_COUNT}.json"
    limited_path.write_text(
        json.dumps(selected_signals, indent=2, ensure_ascii=False),
        encoding="utf-8"
    )
    print(f"  Wrote raw limited subset → {limited_path}")

    # 2. Label the selected subset (fast dict mode)
    labelled_path = PERSISTENT_CACHE_DIR / f"VSS_LABELLED_{TEST_SIGNAL_COUNT}.json"

    if labelled_path.exists():
        print(f"[LABELLING] Using cached labelled data: {labelled_path}")
        with open(labelled_path, "r", encoding="utf-8") as f:
            labelled_data = json.load(f)
        print(f"  Loaded {len(labelled_data)} labelled signals from cache")
    else:
        print("[LABELLING] Labelling the selected subset (fast mode)...")
        labelling_agent = VSSLabellingAgent()
        labelled_data = labelling_agent.run_on_dict(selected_signals)

        # Save labelled version
        labelled_path.write_text(
            json.dumps(labelled_data, indent=2, ensure_ascii=False),
            encoding="utf-8"
        )
        print(f"[LABELLING] Saved labelled data → {labelled_path}")

    if len(labelled_data) != len(selected_signals):
        print(f"[WARNING] Labelling returned {len(labelled_data)} items "
              f"(expected {len(selected_signals)})")

    # 3. Convert **labelled** data to YAML
    print("\n[YAML] Converting **labelled** subset to HAL YAML spec...")
    yaml_spec, prop_count = vss_to_yaml_spec(
        vss_json_path=str(labelled_path),           # ← FIXED: use labelled, not raw
        include_prefixes=None,
        max_props=None,
        vendor_namespace=VENDOR_NAMESPACE,
        add_meta=True,
    )

    spec_path = OUTPUT_DIR / f"SPEC_FROM_VSS_{TEST_SIGNAL_COUNT}.yaml"
    spec_path.write_text(yaml_spec, encoding="utf-8")
    print(f"  Wrote {spec_path} with {prop_count} properties")

    # 4. Load spec
    print("[2/5] Loading HAL spec...")
    full_spec = load_hal_spec_from_yaml_text(yaml_spec)
    all_properties = full_spec.properties

    # 5. Module planning
    print("[3/5] Running Module Planner...")
    try:
        module_signal_map = plan_modules_from_spec(yaml_spec)
        total = sum(len(v) for v in module_signal_map.values())
        print(f"  → {len(module_signal_map)} modules, {total} signals total")
        print("  Modules:", ", ".join(module_signal_map.keys()))
    except Exception as e:
        print(f"[ERROR] Planner failed: {e}")
        return

    # 6. Generate modules
    print(f"[4/5] Generating {len(module_signal_map)} HAL modules...")
    architect = ArchitectAgent()

    prop_lookup = {}
    for p in all_properties:
        pid = (getattr(p, "property_id", None) or
               getattr(p, "prop_id", None) or
               getattr(p, "id", None) or
               getattr(p, "name", None))
        if pid:
            prop_lookup[pid] = p

    for domain, signal_ids in module_signal_map.items():
        if not signal_ids:
            continue
        module_props = [prop_lookup.get(sid) for sid in signal_ids if sid in prop_lookup]
        if not module_props:
            continue

        print(f"\n{'='*60}")
        print(f"  MODULE: {domain.upper()} ({len(module_props)} props)")
        print(f"{'='*60}")

        module_spec = ModuleSpec(domain=domain, properties=module_props)
        try:
            architect.run(module_spec)
            print("  → OK")
        except Exception as e:
            print(f"  → FAILED: {e}")

    print("\nAll HAL module drafts generated (inside project/.llm_draft/)")

    # 7. Supporting components (all inside project)
    print("[5/5] Generating supporting components...")
    DesignDocAgent().run(module_signal_map, all_properties, yaml_spec)
    PromoteDraftAgent().run()
    generate_selinux(full_spec)
    BuildGlueAgent().run()
    LLMAndroidAppAgent().run(module_signal_map, all_properties)
    LLMBackendAgent().run(module_signal_map, all_properties)

    print("\nFinished.")
    print(f"  → Cached input files: {PERSISTENT_CACHE_DIR}")
    print(f"  → All generated outputs: {OUTPUT_DIR.resolve()}")


if __name__ == "__main__":
    main()