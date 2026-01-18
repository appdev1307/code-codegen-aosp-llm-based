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
        domain = (raw_domain or "").strip().upper() if isinstance(raw_domain, str) else raw_domain
        print(f"[ARCHITECT] Domain(raw)={raw_domain} normalized={domain}", flush=True)

        try:
            spec_text = spec.to_llm_spec()
        except Exception as e:
            spec_text = f"[ARCHITECT] WARNING: spec.to_llm_spec() failed: {e}"

        print("[ARCHITECT] Input HAL specification:", flush=True)
        print(spec_text)

        # 0) PLAN (LLM intent-only)
        print("[ARCHITECT] Step 0: Generate HAL plan (LLM intent-only, chunked)", flush=True)
        plan = PlanAgent().run(spec)

        Path("output").mkdir(parents=True, exist_ok=True)
        Path("output/PLAN.json").write_text(json.dumps(plan, indent=2), encoding="utf-8")
        print("[ARCHITECT] Wrote output/PLAN.json", flush=True)

        # Pass PLAN into LLM generators (NOT raw spec)
        plan_text = json.dumps(plan, separators=(",", ":"))

        # 1) AIDL (LLM draft)
        print("[ARCHITECT] Step 1: Generate VHAL AIDL (LLM draft)", flush=True)
        generate_vhal_aidl(plan_text)

        # 2) C++ service (LLM draft)
        print("[ARCHITECT] Step 2: Generate VHAL C++ service (LLM draft)", flush=True)
        generate_vhal_service(plan_text)

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
