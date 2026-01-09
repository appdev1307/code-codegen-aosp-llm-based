from dataclasses import dataclass
from typing import List, Literal, Optional

Domain = Literal["HVAC", "ADAS", "MEDIA", "POWER"]
Access = Literal["READ", "WRITE", "READ_WRITE"]
PropType = Literal["INT", "FLOAT", "BOOLEAN"]

@dataclass
class PropertySpec:
    id: str
    type: PropType
    access: Access
    areas: List[str]

@dataclass
class HalSpec:
    domain: Domain
    aosp_level: int
    properties: List[PropertySpec]
    vendor: Optional[str] = None
