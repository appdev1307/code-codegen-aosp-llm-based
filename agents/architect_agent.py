# FILE: agents/architect_agent.py

import json

from agents.plan_agent import PlanAgent
from agents.promote_agent import PromoteAgent

from agents.vhal_aidl_agent import generate_vhal_aidl
from agents.vhal_service_agent import generate_vhal_service
from agents.vhal_aidl_build_agent import generate_vhal_aidl_bp
from agents.vhal_service_build_agent import generate_vhal_service_build_glue

from agents.car_service_agent import generate_car_service
from agents.selinux_agent import generate_selinux
from tools.safe_writer import SafeWriter


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

        # Step 0: Optional plan (LLM intent-only)
        print("[ARCHITECT] Step 0: Generate HAL plan (LLM intent-only)", flush=True)
        plan = None
        try:
            plan = PlanAgent().run(spec)
        except Exception as e:
            print(f"[ARCHITECT] [WARN] Plan generation failed: {e}. Continue without plan.", flush=True)
            plan = None

        # Persist plan for traceability (if generated)
        try:
            w = SafeWriter("output")
            w.write("PLAN.json", json.dumps(plan or {}, indent=2) + "\n")
            print("[ARCHITECT] Wrote output/PLAN.json", flush=True)
        except Exception as e:
            print(f"[ARCHITECT] [WARN] Could not write PLAN.json: {e}", flush=True)

        # Step 1: Stage 1 LLM draft generation (full files)
        print("[ARCHITECT] Step 1: Generate VHAL AIDL (LLM draft)", flush=True)
        generate_vhal_aidl(spec, plan=plan)

        print("[ARCHITECT] Step 2: Generate VHAL C++ service (LLM draft)", flush=True)
        generate_vhal_service(spec, plan=plan)

        # If you also generate build glue / init / vintf / sepolicy in Stage 1 agents,
        # call them here as well (LLM draft versions).
        # Otherwise your current deterministic generators can still be used for those.
        print("[ARCHITECT] Step 3: Generate build glue (Soong + init rc + VINTF)", flush=True)
        generate_vhal_aidl_bp()
        generate_vhal_service_build_glue()

        print(f"[ARCHITECT] Step 4: Generate framework service (domain={domain})", flush=True)
        if domain == "HVAC":
            generate_car_service(spec)
        else:
            print(f"[ARCHITECT] Domain={domain}: skip framework generation (HVAC-only).", flush=True)

        print("[ARCHITECT] Step 5: Generate SELinux policy", flush=True)
        generate_selinux(spec)

        # Step 6: Stage 2 promotion (gated copy)
        print("[ARCHITECT] Step 6: Promote LLM draft outputs into output/ (gated)", flush=True)
        result = PromoteAgent().run()
        if not result.ok:
            print("[ARCHITECT] [WARN] Promotion failed. See output/STAGE2_REPORT.json", flush=True)
        else:
            print("[ARCHITECT] Promotion succeeded.", flush=True)

        print("[ARCHITECT] ================================")
        print("[ARCHITECT] HAL GENERATION COMPLETED ✅")
        print("[ARCHITECT] ================================")
