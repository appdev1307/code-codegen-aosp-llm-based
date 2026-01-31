from dataclasses import dataclass, field
from typing import Dict, List, Literal, Optional, Any

Domain = Literal["HVAC", "ADAS", "MEDIA", "POWER", "CHASSIS", "BODY", "OTHER"]  # ← expanded for realism
Access = Literal["READ", "WRITE", "READ_WRITE"]
PropType = Literal["INT", "FLOAT", "BOOLEAN"]


@dataclass
class PropertySpec:
    id: str                     # This is the full property name (e.g. "VEHICLE_CHILDREN_ADAS_CHILDREN_ABS_CHILDREN_ISENABLED")
    type: PropType
    access: Access
    areas: List[str]
    # Carry extra metadata from YAML (aosp/sdv/vss_path/constraints/description/etc.)
    meta: Dict[str, Any] = field(default_factory=dict)


@dataclass
class HalSpec:
    domain: Domain
    aosp_level: int
    properties: List[PropertySpec]
    vendor: Optional[str] = None

    def to_llm_spec(self) -> str:
        """Generate a human/LLM-readable summary of this HAL spec."""
        lines = [
            f"HAL Domain: {self.domain}",
            f"AOSP Level: {self.aosp_level}",
            f"Vendor: {self.vendor or 'AOSP'}",
            f"Total Properties: {len(self.properties)}",
            "",
            "Properties:",
        ]
        for p in self.properties:
            areas_str = ", ".join(p.areas) if p.areas else "GLOBAL"
            lines.extend([
                f"- Name: {p.id}",
                f"  Type: {p.type}",
                f"  Access: {p.access}",
                f"  Areas: {areas_str}",
                "",
            ])
        return "\n".join(lines)

    def get_property_by_name(self, name: str) -> Optional[PropertySpec]:
        """Convenience lookup by property name."""
        for prop in self.properties:
            if prop.id == name:
                return prop
        return None

    @property
    def properties_by_name(self) -> Dict[str, PropertySpec]:
        """Cached view of properties keyed by name (lazy computed)."""
        if not hasattr(self, "_properties_by_name"):
            self._properties_by_name = {p.id: p for p in self.properties}
        return self._properties_by_name