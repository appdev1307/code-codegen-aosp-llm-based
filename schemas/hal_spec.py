from dataclasses import dataclass, field
from typing import Dict, List, Literal, Optional, Any


Domain = Literal["HVAC", "ADAS", "MEDIA", "POWER"]
Access = Literal["READ", "WRITE", "READ_WRITE"]
PropType = Literal["INT", "FLOAT", "BOOLEAN"]


@dataclass
class PropertySpec:
    id: str
    type: PropType
    access: Access
    areas: List[str]

    # ✅ NEW: carry extra metadata from YAML (AOSP/SDV/constraints/etc.)
    meta: Dict[str, Any] = field(default_factory=dict)


@dataclass
class HalSpec:
    domain: Domain
    aosp_level: int
    properties: List[PropertySpec]
    vendor: Optional[str] = None

    def to_llm_spec(self) -> str:
        lines = [
            f"HAL Domain: {self.domain}",
            f"AOSP Level: {self.aosp_level}",
            f"Vendor    : {self.vendor or 'AOSP'}",
            "",
            "Properties:",
        ]

        for p in self.properties:
            lines.extend([
                f"- Property ID : {p.id}",
                f"  Type        : {p.type}",
                f"  Access      : {p.access}",
                f"  Areas       : {', '.join(p.areas) if p.areas else 'GLOBAL'}",
                "",
            ])

        return "\n".join(lines)
