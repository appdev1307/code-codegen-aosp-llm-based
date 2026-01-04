from validator.vhal_validator import VHALValidator


def validate_all(vhal_code: str, car_service_code: str, selinux_policy: str):
    errors = []

    # --- VHAL ---
    print("[VALIDATOR] Validating VHAL...", flush=True)
    vhal_validator = VHALValidator()
    vhal_result = vhal_validator.validate(vhal_code)
    print(vhal_result)

    if not vhal_result.ok:
        errors.extend(vhal_result.errors)

    # --- CarService (basic heuristic for now) ---
    print("[VALIDATOR] Validating CarService...", flush=True)
    if "CarPropertyManager" not in car_service_code:
        errors.append("CarService missing CarPropertyManager usage")

    # --- SELinux ---
    print("[VALIDATOR] Validating SELinux policy...", flush=True)
    if "type car_hvac_service" not in selinux_policy:
        errors.append("SELinux policy missing car_hvac_service type")

    if errors:
        print("[VALIDATOR] ❌ VALIDATION FAILED", flush=True)
        for e in errors:
            print(f" - {e}", flush=True)
        raise RuntimeError("Validation failed")

    print("[VALIDATOR] ✅ ALL VALIDATIONS PASSED", flush=True)
