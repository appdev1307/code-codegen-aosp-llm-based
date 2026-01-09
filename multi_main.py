from schemas.hal_spec import HalSpec, PropertySpec
from agents.architect_agent import ArchitectAgent

spec = HalSpec(
    domain="HVAC",
    aosp_level=13,
    vendor="vinfast",
    properties=[
        PropertySpec(
            id="HVAC_TEMPERATURE_SET",
            type="FLOAT",
            access="READ_WRITE",
            areas=["ROW_1_LEFT", "ROW_1_RIGHT"],
        )
    ],
)

ArchitectAgent().run(spec)
