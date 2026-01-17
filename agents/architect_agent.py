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

        # Always read domain late (after any upstream normalization) and normalize it
        raw_domain = getattr(spec, "domain", None)
        domain = (raw_domain or "").strip().upper() if isinstance(raw_domain, str) else raw_domain
        print(f"[ARCHITECT] Domain(raw)={raw_domain} normalized={domain}", flush=True)

        # Helpful visibility for debugging wrong domain mapping
        try:
            spec_text = spec.to_llm_spec()
        except Exception as e:
            spec_text = f"[ARCHITECT] WARNING: spec.to_llm_spec() failed: {e}"
        print("[ARCHITECT] Input HAL specification:", flush=True)
        print(spec_text)

        # 1) AIDL
        print("[ARCHITECT] Step 1: Generate VHAL AIDL", flush=True)
        generate_vhal_aidl(spec)

        # 2) C++ service
        print("[ARCHITECT] Step 2: Generate VHAL C++ service", flush=True)
        generate_vhal_service(spec)

        # 3) Build glue
        print("[ARCHITECT] Step 3: Generate build glue (Soong + init rc + VINTF)", flush=True)
        generate_vhal_aidl_bp()
        generate_vhal_service_build_glue()

        # 4) Framework service generation (HVAC only)
        print(f"[ARCHITECT] Step 4: Generate framework service (domain={domain})", flush=True)

        if domain == "HVAC":
            generate_car_service(spec)
        else:
            print(f"[ARCHITECT] Domain={domain}: skip framework generation (HVAC-only).", flush=True)

        # 5) SELinux
        print("[ARCHITECT] Step 5: Generate SELinux policy", flush=True)
        generate_selinux(spec)

        print("[ARCHITECT] ================================")
        print("[ARCHITECT] HAL GENERATION COMPLETED ✅")
        print("[ARCHITECT] ================================")
