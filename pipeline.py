from agents.vhal_agent import generate_vhal
from agents.car_service_agent import generate_car_service
from agents.selinux_agent import generate_selinux
from validator import validate_all


def run_pipeline():
    print("[DEBUG] Pipeline running...", flush=True)

    spec = """
    Property: VEHICLE_SPEED
    Type: float
    Access: read
    Permission: android.car.permission.CAR_SPEED
    """

    print("[DEBUG] Step 1: Generate VHAL", flush=True)
    vhal_path = generate_vhal(spec)

    print("[DEBUG] Step 2: Generate CarService", flush=True)
    car_service_path = generate_car_service(spec)

    print("[DEBUG] Step 3: Generate SELinux", flush=True)
    selinux_policy_path = generate_selinux(spec)

    print("[DEBUG] Step 4: Validate all artifacts", flush=True)
    validate_all(
        vhal_path,
        car_service_path,
        selinux_policy_path
    )

    print("[DEBUG] Pipeline finished successfully", flush=True)
