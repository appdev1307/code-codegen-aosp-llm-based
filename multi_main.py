# main.py - With CACHE_MODE switch: "drive" (Colab) or "local" (your PC)
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


# === CONFIGURATION ===
CACHE_MODE = "local"          # "drive" = Google Drive (Colab) | "local" = local folder (your PC)
TEST_SIGNAL_COUNT = 50        # Number of signals for test (set to None for full run)
# =======================


# Helper to flatten VSS
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
        # ... (giữ nguyên như cũ) ...
        pass


def main():
    vss_path = "./dataset/vss.json"
    output_dir = Path("output")
    output_dir.mkdir(parents=True, exist_ok=True)

    mode_desc = "full dataset" if TEST_SIGNAL_COUNT is None else f"{TEST_SIGNAL_COUNT}-signal test"
    print(f"🚀 Starting VSS → AAOS HAL Generation ({mode_desc}) — Cache: {CACHE_MODE.upper()}")

    # === Setup cache path ===
    if CACHE_MODE == "drive":
        from google.colab import drive
        drive.mount('/content/drive')
        cache_dir = Path("/content/drive/MyDrive/vss_hal_cache")
    else:  # local
        cache_dir = '../cache-llm'

    cache_dir.mkdir(parents=True, exist_ok=True)

    suffix = "" if TEST_SIGNAL_COUNT is None else f"_{TEST_SIGNAL_COUNT}"
    labelled_cache_path = cache_dir / f"VSS_LABELLED{suffix}.json"

    # === Load or generate labelled data ===
    if labelled_cache_path.exists():
        print(f"[CACHE] Loading labelled data from {CACHE_MODE} cache: {labelled_cache_path}")
        with open(labelled_cache_path, "r", encoding="utf-8") as f:
            labelled_data = json.load(f)
        print(f"[CACHE] Loaded {len(labelled_data)} labelled signals")
    else:
        print("[LABELLING] Cache not found — running LLM labelling...")

        with open(vss_path, "r", encoding="utf-8") as f:
            raw_vss = json.load(f)

        leaf_signals = flatten_vss(raw_vss)
        print(f"[DATA] Found {len(leaf_signals)} leaf signals")

        # Apply test limit
        if TEST_SIGNAL_COUNT is not None:
            signals_to_label = dict(list(leaf_signals.items())[:TEST_SIGNAL_COUNT])
            print(f"[TEST] Labelling only {TEST_SIGNAL_COUNT} signals")
        else:
            signals_to_label = leaf_signals
            print("[FULL] Labelling all signals")

        labelling_agent = VSSLabellingAgent()
        labelled_data = labelling_agent.run_on_dict(signals_to_label)

        # Save cache
        labelled_cache_path.write_text(json.dumps(labelled_data, indent=2, ensure_ascii=False))
        print(f"[CACHE] Saved labelled data to {CACHE_MODE} cache: {labelled_cache_path}")

    # === Convert to YAML ===
    yaml_spec, n = vss_to_yaml_spec(
        vss_json=labelled_data,
        include_prefixes=None,
        max_props=None,
        vendor_namespace="vendor.vss",
        add_meta=True,
    )

    spec_path = output_dir / "SPEC_FROM_VSS.yaml"
    spec_path.write_text(yaml_spec, encoding="utf-8")
    print(f"[DEBUG] Wrote {spec_path} with {n} labelled properties")

    # === Rest of pipeline (unchanged) ===
    full_spec = load_hal_spec_from_yaml_text(yaml_spec)
    all_properties = full_spec.properties

    print("\n[MODULE PLANNER] Running...")
    module_signal_map = plan_modules_from_spec(yaml_spec)
    print(f"LLM identified {len(module_signal_map)} modules")

    # ... (property lookup, generation, promotion, full stack — giữ nguyên như cũ) ...

    print("\n🎉 Run complete!")
    if TEST_SIGNAL_COUNT is not None:
        print(f"    → Test mode with {TEST_SIGNAL_COUNT} signals")
        print("    → For full run: set TEST_SIGNAL_COUNT = None")
    print(f"    → Cache mode: {CACHE_MODE.upper()}")


if __name__ == "__main__":
    main()