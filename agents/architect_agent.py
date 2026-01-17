# FILE: agents/architect_agent.py (REPLACE run() BODY SECTION)

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
        print("[ARCHITECT] Input HAL specification:")
        print(spec.to_llm_spec())

        # 1) VHAL AIDL
        generate_vhal_aidl(spec)

        # 2) VHAL C++ service
        generate_vhal_service(spec)

        # 3) Build glue (AIDL Android.bp + service Android.bp + rc + vintf)
        generate_vhal_aidl_bp(spec)
        generate_vhal_service_build_glue(spec)

        # 4) Framework generation (HVAC only)
        if getattr(spec, "domain", None) == "HVAC":
            generate_car_service(spec)
        else:
            print(f"[ARCHITECT] Domain={getattr(spec, 'domain', None)}: skip framework generation (HVAC-only).", flush=True)

        # 5) SELinux
        generate_selinux(spec)

        print("[ARCHITECT] ================================")
        print("[ARCHITECT] HAL GENERATION COMPLETED ✅")
        print("[ARCHITECT] ================================")
