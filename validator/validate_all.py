from validator.aidl_validator import validate_aidl
from validator.vhal_service_validator import validate_vhal_service
from validator.aidl_service_contract_validator import (
    validate_aidl_service_contract,
)


def validate_all(aidl, vhal_service, car_service, sepolicy):
    issues = []

    # ---------- AIDL ----------
    if not aidl:
        issues.append("AIDL: Missing AIDL definition")
    else:
        issues += validate_aidl(aidl)

    # ---------- VHAL Service ----------
    if not vhal_service:
        issues.append("VHAL Service: Missing implementation")
    else:
        issues += validate_vhal_service(vhal_service)

    # ---------- AIDL ↔ Service Contract ----------
    if aidl and vhal_service:
        issues += validate_aidl_service_contract(aidl, vhal_service)

    # ---------- CarService ----------
    if not car_service:
        issues.append("CarService: Missing CarService implementation")
    else:
        if "CarPropertyManager" not in car_service:
            issues.append("CarService: Missing CarPropertyManager usage")

    # ---------- SELinux ----------
    if not sepolicy:
        issues.append("SELinux: Missing SEPolicy file")
    else:
        if "type car_hvac_service" not in sepolicy:
            issues.append("SELinux: Missing car_hvac_service type")

    return issues
