from validator.vhal_validator import VHALValidator


def validate_all(vhal_code: str, car_service_code: str, sepolicy_code: str):
    issues = []

    # ===== VHAL checks =====
    if "android/hardware/automotive/vehicle" not in vhal_code:
        issues.append("Missing required include: android/hardware/automotive/vehicle")

    if "VehiclePropValue" not in vhal_code:
        issues.append("Missing required VHAL symbol: VehiclePropValue")

    # ===== CarService checks =====
    if "CarPropertyManager" not in car_service_code:
        issues.append("CarService missing CarPropertyManager usage")

    # ===== SELinux checks =====
    if "type car_hvac_service" not in sepolicy_code:
        issues.append("SELinux policy missing car_hvac_service type")

    return issues
