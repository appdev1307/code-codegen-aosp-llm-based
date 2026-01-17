# FILE: agents/architect_agent.py

from agents.vhal_aidl_agent import generate_vhal_aidl
from agents.vhal_service_agent import generate_vhal_service
from agents.vhal_aidl_build_agent import generate_vhal_aidl_bp
from agents.vhal_service_build_agent import generate_vhal_service_build_glue

from agents.car_service_agent import generate_car_service
from agents.selinux_agent import generate_selinux


class ArchitectAgent:
    def run(self, spec):
        print("[ARCHITECT] ================================")
        print("[ARCHITECT] AAOS HAL Architect Agent START")
        print("[ARCHITECT] ================================")

        domain = getattr(spec, "domain", None)
        print(f"[ARCHITECT] Domain: {domain}", flush=True)

        print("[ARCHITECT] Input HAL specification:")
        print(spec.to_llm_spec())

        # 1) AIDL
        print("[ARCHITECT] Step 1: Generate VHAL AIDL", flush=True)
        generate_vhal_aidl(spec)

        # 2) C++ service
        print("[ARCHITECT] Step 2: Generate VHAL C++ service", flush=True)
        generate_vhal_service(spec)

        # 3) Build glue
        print("[ARCHITECT] Step 3: Generate build glue (Soong + init rc + VINTF)", flush=True)
        generate_vhal_aidl_bp(spec)
        generate_vhal_service_build_glue(spec)

        # 4) Framework (HVAC only)
        if domain == "HVAC":
            print("[ARCHITECT] Step 4: Generate framework service (HVAC)", flush=True)
            generate_car_service(spec)
        else:
            print(f"[ARCHITECT] Step 4: Skip framework generation (domain={domain}, HVAC-only).", flush=True)

        # 5) SELinux
        print("[ARCHITECT] Step 5: Generate SELinux policy", flush=True)
        generate_selinux(spec)

        print("[ARCHITECT] ================================")
        print("[ARCHITECT] HAL GENERATION COMPLETED ✅")
        print("[ARCHITECT] ================================")
