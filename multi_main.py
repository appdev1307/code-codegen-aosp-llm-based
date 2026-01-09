from agents.spec_yaml_converter_agent import SpecYamlConverterAgent
from schemas.yaml_loader import load_hal_spec_from_yaml_text
from agents.architect_agent import ArchitectAgent
from tools.aosp_layout import ensure_aosp_layout


designer_simple_spec = """
System: Climate Control
Platform: AAOS

Features:
- Dual zone climate
- Seat heating linked with HVAC
- Cabin temperature display

Properties:
- Property ID : HVAC_TEMPERATURE_SET
  Type        : FLOAT
  Access      : READ_WRITE
  Areas       : ROW_1_LEFT, ROW_1_RIGHT

- Property ID : HVAC_SEAT_HEAT_LEVEL
  Type        : INT32
  Access      : READ_WRITE
  Areas       : ROW_1_LEFT, ROW_1_RIGHT
  Range       : 0..3 step 1

- Property ID : HVAC_CABIN_TEMPERATURE
  Type        : FLOAT
  Access      : READ
  Areas       : ROW_1_LEFT, ROW_1_RIGHT
""".strip()


# 1) Convert designer text -> standardized YAML
yaml_spec = SpecYamlConverterAgent(output_root="output").run(designer_simple_spec)

# 2) YAML -> your existing HalSpec/PropertySpec
spec = load_hal_spec_from_yaml_text(yaml_spec)

# 3) Existing pipeline (unchanged)
ensure_aosp_layout(spec)
ArchitectAgent().run(spec)
