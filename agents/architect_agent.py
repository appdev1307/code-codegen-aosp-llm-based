from schemas.hal_spec import HalSpec
from agents.vhal_aidl_agent import generate_vhal_aidl
from agents.vhal_service_agent import generate_vhal_service
from agents.car_service_agent import generate_car_service
from agents.selinux_agent import generate_selinux


class ArchitectAgent:
    def run(self, spec: HalSpec):
        print("[ARCHITECT] ================================")
        print("[ARCHITECT] AAOS HAL Architect Agent START")
        print("[ARCHITECT] ================================")

        print("[ARCHITECT] Input HalSpec received")
        print(f"[ARCHITECT]   Domain      : {spec.domain}")
        print(f"[ARCHITECT]   AOSP Level  : {spec.aosp_level}")
        print(f"[ARCHITECT]   Vendor      : {spec.vendor}")
        print(f"[ARCHITECT]   Properties  : {len(spec.properties)}")

        for p in spec.properties:
            print("[ARCHITECT]   └─ Property:")
            print(f"[ARCHITECT]        id     = {p.id}")
            print(f"[ARCHITECT]        type   = {p.type}")
            print(f"[ARCHITECT]        access = {p.access}")
            print(f"[ARCHITECT]        areas  = {p.areas}")

        print("[ARCHITECT] Deriving HAL architecture from AOSP standards...")

        # Architecture decision is implicit via agent selection
        print("[ARCHITECT] Dispatching generation agents...")

        aidl = generate_vhal_aidl(spec)
        vhal = generate_vhal_service(spec)
        car  = generate_car_service(spec)
        se   = generate_selinux(spec)

        print("[ARCHITECT] ================================")
        print("[ARCHITECT] HAL GENERATION COMPLETED ✅")
        print("[ARCHITECT] ================================")

        return {
            "aidl": aidl,
            "vhal": vhal,
            "car": car,
            "sepolicy": se,
        }
