# FILE: agents/architect_agent.py
import json
from pathlib import Path
from agents.plan_agent import PlanAgent
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

        raw_domain = getattr(spec, "domain", None)
        domain = (raw_domain or "").strip().upper() if isinstance(raw_domain, str) else "UNKNOWN"
        print(f"[ARCHITECT] Domain(raw)={raw_domain} normalized={domain}")

        try:
            spec_text = spec.to_llm_spec()
        except Exception as e:
            spec_text = f"[ARCHITECT] WARNING: spec.to_llm_spec() failed: {e}"
        print("[ARCHITECT] Input HAL specification:")
        print(spec_text)

        # 0) PLAN (LLM intent-only)
        print("[ARCHITECT] Step 0: Generate HAL plan (LLM intent-only, chunked)")
        plan_agent = PlanAgent()
        plan = plan_agent.run(spec)
        Path("output").mkdir(parents=True, exist_ok=True)
        plan_path = Path("output/PLAN.json")
        plan_path.write_text(json.dumps(plan, indent=2, ensure_ascii=False), encoding="utf-8")
        print("[ARCHITECT] Wrote output/PLAN.json")

        # Pass compact PLAN to downstream LLM agents
        plan_text = json.dumps(plan, separators=(",", ":"))  # compact for token savings

        # 1) AIDL (LLM draft)
        print("[ARCHITECT] Step 1: Generate VHAL AIDL (LLM draft)")
        aidl_success = generate_vhal_aidl(plan_text)
        print("[AIDL] Generated successfully!" if aidl_success else "[WARN] AIDL used fallback")

        # 2) C++ service (LLM draft)
        print("[ARCHITECT] Step 2: Generate VHAL C++ service (LLM draft)")
        service_success = generate_vhal_service(plan_text)
        print("[VHAL SERVICE] Generated successfully!" if service_success else "[WARN] VHAL Service used fallback")

        # 3) Build glue
        print("[ARCHITECT] Step 3: Generate build glue (Soong + init rc + VINTF)")
        generate_vhal_aidl_bp()
        generate_vhal_service_build_glue()
        print("[BUILD] Glue generated")

        # 4) Framework service generation
        print(f"[ARCHITECT] Step 4: Generate framework service (domain={domain})")
        # Currently HVAC-only, but easy to extend
        if domain == "HVAC":
            generate_car_service(spec)
            print("[CAR SERVICE] Generated CarHvacService.java")
        else:
            print(f"[ARCHITECT] Skipping framework service generation (only HVAC supported for now)")

        # 5) SELinux
        print("[ARCHITECT] Step 5: Generate SELinux policy")
        generate_selinux(spec)
        print("[SELINUX] Policy generated")

        print("[ARCHITECT] ================================")
        print("[ARCHITECT] HAL GENERATION COMPLETED ✅")
        print("[ARCHITECT] ================================")