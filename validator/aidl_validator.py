def validate_aidl(aidl: str):
    issues = []

    if "interface IVehicle" not in aidl:
        issues.append("AIDL: IVehicle interface missing")

    if "parcelable VehiclePropValue" not in aidl:
        issues.append("AIDL: VehiclePropValue parcelable missing")

    return issues
