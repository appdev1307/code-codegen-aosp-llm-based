import re


def validate_aidl_service_contract(aidl_code: str, service_code: str):
    issues = []


    if "interface IVehicle" in aidl and "IVehicle" not in vhal_service:
        issues.append(
            "Contract: VHAL service does not implement IVehicle AIDL"
        )

    # ===== 1. Detect AIDL interface name =====
    m = re.search(r'interface\s+(\w+)', aidl_code)
    if not m:
        issues.append("Contract: Cannot find AIDL interface name")
        return issues

    interface = m.group(1)

    # ===== 2. Required inheritance =====
    if f"Bn{interface}" not in service_code:
        issues.append(
            f"Contract: Service does not inherit from Bn{interface}"
        )

    # ===== 3. Extract AIDL method names =====
    aidl_methods = re.findall(
        r'\b(\w+)\s*\(.*?\)\s*;', aidl_code
    )

    if not aidl_methods:
        issues.append("Contract: No methods found in AIDL interface")
        return issues

    # ===== 4. Check each method implemented =====
    for method in aidl_methods:
        # allow Status method(...) or ndk::ScopedAStatus
        if not re.search(rf'\b{method}\s*\(', service_code):
            issues.append(
                f"Contract: Service missing implementation of '{method}()'"
            )

    # ===== 5. VehiclePropValue consistency =====
    if "VehiclePropValue" in aidl_code:
        if "VehiclePropValue" not in service_code:
            issues.append(
                "Contract: VehiclePropValue used in AIDL but not in service"
            )

    # ===== 6. AIDL namespace sanity =====
    if "aidl::android::hardware::automotive::vehicle" not in service_code:
        issues.append(
            "Contract: Service missing AIDL vehicle namespace"
        )

    return issues
