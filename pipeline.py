from agents.vhal_agent import generate_vhal
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
"""

    error_context = ""

    for attempt in range(1, MAX_RETRY + 1):
        print(f"\n[DEBUG] Attempt {attempt}", flush=True)

        # -------- Build spec with feedback --------
        spec = base_spec
        if error_context:
            spec += f"""

PREVIOUS ATTEMPT FAILED WITH ERRORS:
{error_context}

MANDATORY FIX IN THIS ATTEMPT:
- Fix ALL errors listed above
- DO NOT repeat previous mistakes
- Generated code MUST pass validation
"""

        try:
            # -------- Step 1: VHAL --------
            print("[DEBUG] Step 1: Generate VHAL", flush=True)
            vhal_code = generate_vhal(spec)

            # -------- Step 2: CarService --------
            print("[DEBUG] Step 2: Generate CarService", flush=True)
            car_service_code = generate_car_service(spec)

            # -------- Step 3: SELinux --------
            print("[DEBUG] Step 3: Generate SELinux", flush=True)
            selinux_policy = generate_selinux(spec)

            # -------- Step 4: Validate --------
            print("[DEBUG] Step 4: Validate all artifacts", flush=True)
            validate_all(
                vhal=vhal_code,
                service=car_service_code,
                sepolicy=selinux_policy,
            )

            print("\n[DEBUG] ✅ PIPELINE PASSED", flush=True)
            return

        except RuntimeError as e:
            print("[DEBUG] ❌ VALIDATION FAILED", flush=True)
            error_context = str(e)

    # -------- Final failure --------
    raise RuntimeError("Validation failed after max retries")
