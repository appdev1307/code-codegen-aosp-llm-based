from vss_to_yaml import vss_to_yaml_spec
from schemas.yaml_loader import load_hal_spec_from_yaml_text
from agents.architect_agent import ArchitectAgent
from tools.aosp_layout import ensure_aosp_layout

yaml_spec = vss_to_yaml_spec(
    vss_json_path="./dataset/vss.json",
    include_prefixes=None,
    max_props=200,
    vendor_namespace="vendor.vss",
)

spec = load_hal_spec_from_yaml_text(yaml_spec)

ensure_aosp_layout(spec)
ArchitectAgent().run(spec)
