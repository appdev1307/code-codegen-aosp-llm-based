from dataclasses import dataclass
from typing import List
from schemas.hal_spec import HalSpec


@dataclass
class SystemSpec:
    hals: List[HalSpec]
