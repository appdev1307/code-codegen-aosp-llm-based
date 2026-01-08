from validator.aidl_validator import validate_aidl
from validator.vhal_service_validator import validate_vhal_service
from validator.aidl_service_contract_validator import (
    validate_aidl_service_contract
)


def validate_all(aidl, vhal_service, car_service, sepolicy):
    issues = []

    issues += validate_aidl(aidl)
    issues += validate_vhal_service(vhal_service)
    issues += validate_aidl_service_contract(aidl, vhal_service)

    if "CarPropertyManager" not in car_service:
        issues.append("CarService: Missing CarPropertyManager usage")

    if "type car_hvac_service" not in sepolicy:
        issues.append("SELinux: Missing car_hvac_service type")

    return issues
