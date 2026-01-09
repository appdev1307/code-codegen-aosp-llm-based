import re


def validate_aidl_service_contract(aidl_code: str, service_code: str):
    issues = []

    # Defensive: empty artifacts
    if not aidl_code or not service_code:
        return issues

    # ===== 1. Detect AIDL interface name =====
    m = re.search(r'\binterface\s+(\w+)\b', aidl_code)
    if not m:
        issues.append("Contract: Cannot find AIDL interface name")
        return issues

    interface = m.group(1)

    # ===== 2. Service must inherit from generated Bn interface =====
    if f"Bn{interface}" not in service_code:
        issues.append(
            f"Contract: Service does not inherit from Bn{interface}"
        )

    # ===== 3. Extract AIDL method names =====
    aidl_methods = re.findall(
        r'\b(?:oneway\s+)?\w+\s+(\w+)\s*\([^)]*\)\s*;',
        aidl_code
    )

    if not aidl_methods:
        issues.append("Contract: No methods found in AIDL interface")
        return issues

    # ===== 4. Each AIDL method must be implemented =====
    for method in aidl_methods:
        if not re.search(rf'\b{method}\s*\(', service_code):
            issues.append(
                f"Contract: Service missing implementation of '{method}()'"
            )

    # ===== 5. VehiclePropValue consistency =====
    if "VehiclePropValue" in aidl_code and "VehiclePropValue" not in service_code:
        issues.append(
            "Contract: VehiclePropValue used in AIDL but not in service"
        )

    # ===== 6. AIDL vehicle namespace sanity =====
    if "aidl::android::hardware::automotive::vehicle" not in service_code:
        issues.append(
            "Contract: Service missing AIDL vehicle namespace"
        )

    return issues
