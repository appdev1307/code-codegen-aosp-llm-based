def validate_aidl(aidl: str):
    issues = []

    if "IVehicle.aidl" not in aidl:
        issues.append("AIDL: Missing IVehicle.aidl")

    if "Android.bp" not in aidl:
        issues.append("AIDL: Missing Android.bp")

    return issues
