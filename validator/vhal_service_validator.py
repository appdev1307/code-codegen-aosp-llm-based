def validate_vhal_service(service_code: str):
    issues = []

    if "BnVehicle" not in service_code:
        issues.append("VHAL Service: Missing BnVehicle inheritance")

    if "get(" not in service_code:
        issues.append("VHAL Service: Missing get() implementation")

    if "set(" not in service_code:
        issues.append("VHAL Service: Missing set() implementation")

    if "registerAsService" not in service_code:
        issues.append("VHAL Service: Missing registerAsService()")

    if "VehiclePropValue" not in service_code:
        issues.append("VHAL Service: VehiclePropValue not used")

    if "VehicleHal" not in vhal_service:
        issues.append("VHAL Service: VehicleHal implementation missing")

    return issues
