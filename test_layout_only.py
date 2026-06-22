import json
from pathlib import Path
from agents.rag_dspy_android_app_agent import RAGDSPyAndroidAppAgent
from schemas.yaml_loader import load_hal_spec_from_yaml_text
from vss_to_yaml import vss_to_yaml_spec
from agents.vss_labelling_agent import flatten_vss

start_ollama()
import time
time.sleep(10)

# Load from cached labelled signals (fast)
print("Loading labelled signals...")
labelled_path = Path("/content/vss_temp/VSS_LABELLED_500.json")
with open(labelled_path, "r", encoding="utf-8") as f:
    labelled_data = json.load(f)

# Take one large domain for fast test (CABIN)
yaml_spec, _ = vss_to_yaml_spec(vss_json_path=str(labelled_path), 
                               vendor_namespace="vendor.vss", 
                               max_props=168)  # limit to CABIN size

full_spec = load_hal_spec_from_yaml_text(yaml_spec)
module_signal_map = {"CABIN": list(labelled_data.keys())[:168]}

print(f"Testing Android App + Layout for CABIN ({len(module_signal_map['CABIN'])} props)...")

# Run only Android App agent
agent = RAGDSPyAndroidAppAgent(
    output_root="output_rag_dspy_test",
    dspy_programs_dir="dspy_opt/saved",
    rag_top_k=8,
    rag_db_path="rag/chroma_db"
)
agent.run(module_signal_map, full_spec.properties)

print("\nRunning fixer...")
from multi_main_rag_dspy import fix_android_layouts   # import from your C3 script
fix_android_layouts("output_rag_dspy_test")

print("\n✅ Fast test done. Check folder: output_rag_dspy_test/hmi_app/src/main/res/layout/")