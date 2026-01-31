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
from agents.vss_labelling_agent import VSSLabellingAgent, flatten_vss
from tools.aosp_layout import ensure_aosp_layout


# ────────────────────────────────────────────────
# Configurable parameters
# ────────────────────────────────────────────────
TEST_SIGNAL_COUNT = 50                  # ← change here to test with different sizes
VSS_PATH          = "./dataset/vss.json"
VENDOR_NAMESPACE  = "vendor.vss"

# Persistent cache folder — only for input-like files
PERSISTENT_CACHE_DIR = Path("/content/vss_temp")
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

    def to_llm_spec(self):
        """Format module specification into LLM-friendly text"""
        lines = [
            f"HAL Domain: {self.domain}",
            f"AOSP Level: {self.aosp_level}",
            f"Vendor: {self.vendor}",
            f"Properties: {len(self.properties)}",
            ""
        ]
        for prop in self.properties:
            # Try different possible property ID fields (very common in VSS/AAOS code)
            prop_id = (
                getattr(prop, "property_id", None) or
                getattr(prop, "prop_id", None) or
                getattr(prop, "id", None) or
                getattr(prop, "name", "UNKNOWN")
            )
            typ = getattr(prop, "type", "UNKNOWN")
            access = getattr(prop, "access", "READ_WRITE")
            areas = getattr(prop, "areas", "GLOBAL")
            areas_str = ", ".join(areas) if isinstance(areas, (list, tuple)) else str(areas)

            lines += [
                f"- Property ID: {prop_id}",
                f"  Type: {typ}",
                f"  Access: {access}",
                f"  Areas: {areas_str}",
                ""
            ]
        return "\n".join(lines)


def main():
    OUTPUT_DIR.mkdir(parents=True, exist_ok=True)
    print(f"Starting VSS → AAOS HAL Generation ({TEST_SIGNAL_COUNT} signals test)")
    print(f"  Persistent cache: {PERSISTENT_CACHE_DIR}")
    print(f"  Project output  : {OUTPUT_DIR.resolve()}\n")

    # 1. Load VSS → flatten to leaf signals → select first N leaves
    print(f"[PREP] Loading and flattening {VSS_PATH} ...")
    try:
        with open(VSS_PATH, "r", encoding="utf-8") as f:
            raw_vss = json.load(f)

        all_leaves = flatten_vss(raw_vss)
        print(f"  Flattened to {len(all_leaves)} leaf signals")

    except Exception as e:
        print(f"Cannot load or flatten {VSS_PATH}: {e}")
        return

    if len(all_leaves) < TEST_SIGNAL_COUNT:
        print(f"Warning: only {len(all_leaves)} leaf signals available (requested {TEST_SIGNAL_COUNT})")
        selected_signals = all_leaves
    else:
        sorted_paths = sorted(all_leaves.keys())
        selected_paths = sorted_paths[:TEST_SIGNAL_COUNT]
        selected_signals = {path: all_leaves[path] for path in selected_paths}

    print(f"Selected {len(selected_signals)} leaf signals for labelling & processing")

    # Write selected flat subset
    limited_path = PERSISTENT_CACHE_DIR / f"VSS_LIMITED_{TEST_SIGNAL_COUNT}.json"
    limited_path.write_text(
        json.dumps(selected_signals, indent=2, ensure_ascii=False),
        encoding="utf-8"
    )
    print(f"  Wrote selected flat subset → {limited_path}")

    # 2. Label the selected subset — with cache invalidation
    labelled_path = PERSISTENT_CACHE_DIR / f"VSS_LABELLED_{TEST_SIGNAL_COUNT}.json"

    need_labelling = True
    if labelled_path.exists():
        if labelled_path.stat().st_mtime >= limited_path.stat().st_mtime:
            print(f"[LABELLING] Using valid cached labelled data: {labelled_path}")
            with open(labelled_path, "r", encoding="utf-8") as f:
                labelled_data = json.load(f)
            print(f"  Loaded {len(labelled_data)} labelled signals from cache")
            need_labelling = False
        else:
            print("[LABELLING] Cache outdated → re-labelling")

    if need_labelling:
        print("[LABELLING] Labelling the selected subset (fast mode)...")
        labelling_agent = VSSLabellingAgent()
        labelled_data = labelling_agent.run_on_dict(selected_signals)

        labelled_path.write_text(
            json.dumps(labelled_data, indent=2, ensure_ascii=False),
            encoding="utf-8"
        )
        print(f"[LABELLING] Saved fresh labelled data → {labelled_path}")

    if len(labelled_data) != len(selected_signals):
        print(f"[WARNING] Labelling returned {len(labelled_data)} items "
              f"(expected {len(selected_signals)}) — continuing anyway")

    # 3. Convert **labelled** data to YAML
    print("\n[YAML] Converting **labelled** subset to HAL YAML spec...")
    yaml_spec, prop_count = vss_to_yaml_spec(
        vss_json_path=str(labelled_path),
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
        print("  Modules:", ", ".join(module_signal_map.keys()) or "(none)")
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

    generated_count = 0
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
            generated_count += 1
        except Exception as e:
            print(f"  → FAILED: {e}")

    print(f"\nAll HAL module drafts generated ({generated_count} modules processed)")

    # 7. Supporting components
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