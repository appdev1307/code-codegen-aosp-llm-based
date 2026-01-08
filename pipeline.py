from agents.vhal_aidl_agent import generate_vhal_aidl
from agents.vhal_service_agent import generate_vhal_service
from agents.car_service_agent import generate_car_service
from agents.selinux_agent import generate_selinux
from validator.validate_all import validate_all


MAX_RETRY = 3


def run_pipeline():
    print("[DEBUG] Pipeline running...", flush=True)

    base_spec = """
Property: VEHICLE_SPEED
Type: float
Access: read
Permission: android.car.permission.CAR_SPEED

Target:
- Android Automotive OS
- AIDL-based Vehicle HAL
"""

    error_context = ""
    artifacts = {
        "aidl": None,
        "vhal_service": None,
        "car_service": None,
        "sepolicy": None,
    }

    for attempt in range(1, MAX_RETRY + 1):
        print(f"\n[DEBUG] Attempt {attempt}", flush=True)

        spec = base_spec
        if error_context:
            spec += f"""

PREVIOUS ERRORS:
{error_context}

MANDATORY:
- Fix ONLY listed errors
- Follow AAOS AIDL Vehicle HAL design
"""

        if artifacts["aidl"] is None:
            artifacts["aidl"] = generate_vhal_aidl(spec)

        if artifacts["vhal_service"] is None:
            artifacts["vhal_service"] = generate_vhal_service(spec)

        if artifacts["car_service"] is None:
            artifacts["car_service"] = generate_car_service(spec)

        if artifacts["sepolicy"] is None:
            artifacts["sepolicy"] = generate_selinux(spec)

        issues = validate_all(
            aidl=artifacts["aidl"],
            vhal_service=artifacts["vhal_service"],
            car_service=artifacts["car_service"],
            sepolicy=artifacts["sepolicy"],
        )

        if not issues:
            print("\n[DEBUG] ✅ PIPELINE PASSED", flush=True)
            return

        error_context = "\n".join(f"- {i}" for i in issues)

        # 🎯 SMART INVALIDATION
        joined = " ".join(issues)
        if "AIDL" in joined or "IVehicle" in joined:
            artifacts["aidl"] = None
        if "VehicleService" in joined:
            artifacts["vhal_service"] = None
        if "CarService" in joined:
            artifacts["car_service"] = None
        if "SELinux" in joined:
            artifacts["sepolicy"] = None

    raise RuntimeError("Validation failed after max retries")
