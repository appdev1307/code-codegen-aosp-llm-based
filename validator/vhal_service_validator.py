# validator/vhal_service_validator.py

def validate_vhal_service(vhal_service: str):
    """
    Validate generated VHAL C++ service implementation.
    """
    issues = []

    if not vhal_service or not isinstance(vhal_service, str):
        issues.append("VHAL Service code is empty or invalid")
        return issues

    # Core class checks
    if "VehicleHal" not in vhal_service:
        issues.append("VHAL Service missing VehicleHal implementation")

    if "IVehicle" not in vhal_service:
        issues.append("VHAL Service does not reference IVehicle interface")

    # Common AAOS patterns
    if "get(" not in vhal_service and "getProperty" not in vhal_service:
        issues.append("VHAL Service missing property getter logic")

    if "set(" not in vhal_service and "setProperty" not in vhal_service:
        issues.append("VHAL Service missing property setter logic")

    # Threading / Looper (basic heuristic)
    if "std::mutex" not in vhal_service and "Mutex" not in vhal_service:
        issues.append("VHAL Service missing concurrency protection (mutex)")

    return issues
