import re
from typing import List, Dict


def _parse_aidl_methods(aidl_code: str) -> List[Dict]:
    """
    Extract method signatures from AIDL.
    Example:
      VehiclePropValue get(int propId, int areaId);
      void set(in VehiclePropValue value);
    """
    methods = []
    pattern = re.compile(
        r'\b(?P<ret>\w+)\s+'
        r'(?P<name>\w+)\s*'
        r'\((?P<params>[^)]*)\)\s*;'
    )

    for m in pattern.finditer(aidl_code):
        params = [
            p.strip().split()[-1]
            for p in m.group("params").split(",")
            if p.strip()
        ]
        methods.append({
            "name": m.group("name"),
            "return": m.group("ret"),
            "params": params,
        })
    return methods


def validate_aidl_service_contract(aidl_code: str, service_code: str):
    issues = []

    if not aidl_code or not service_code:
        return issues

    # ===== Interface name =====
    m = re.search(r'\binterface\s+(\w+)\b', aidl_code)
    if not m:
        issues.append("Contract: Cannot find AIDL interface name")
        return issues

    interface = m.group(1)

    # ===== Inheritance check =====
    if f"Bn{interface}" not in service_code:
        issues.append(
            f"Contract: Service does not inherit from Bn{interface}"
        )

    # ===== Parse AIDL methods =====
    aidl_methods = _parse_aidl_methods(aidl_code)
    if not aidl_methods:
        issues.append("Contract: No methods found in AIDL interface")
        return issues

    # ===== Match each method signature =====
    for m in aidl_methods:
        name = m["name"]
        # loose C++ signature match but strict name check
        if not re.search(rf'\b{name}\s*\(', service_code):
            issues.append(
                f"Contract: Service missing implementation of '{name}()'"
            )

    # ===== VehiclePropValue consistency =====
    if "VehiclePropValue" in aidl_code and "VehiclePropValue" not in service_code:
        issues.append(
            "Contract: VehiclePropValue used in AIDL but not in service"
        )

    # ===== Namespace sanity =====
    if "aidl::android::hardware::automotive::vehicle" not in service_code:
        issues.append(
            "Contract: Service missing AIDL vehicle namespace"
        )

    return issues
