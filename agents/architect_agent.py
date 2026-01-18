# FILE: agents/architect_agent.py

import json

from agents.plan_agent import PlanAgent
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

        # ------------------------------------------------------------------
        # Step 0 (Option C): Generate ONE shared plan (LLM intent only)
        # ------------------------------------------------------------------
        print("[ARCHITECT] Step 0: Generate HAL plan (LLM intent-only)", flush=True)
        plan = None
        try:
            plan = PlanAgent().run(spec)  # should return dict
        except Exception as e:
            # Plan is optional; generation remains deterministic without it
            print(f"[ARCHITECT] [WARN] Plan generation failed: {e}. Continue without plan.", flush=True)
            plan = None

        # Persist plan for traceability (OEM-friendly)
        try:
            w = SafeWriter("output")
            w.write("PLAN.json", json.dumps(plan or {}, indent=2) + "\n")
            print("[ARCHITECT] Wrote output/PLAN.json", flush=True)
        except Exception as e:
            print(f"[ARCHITECT] [WARN] Could not write PLAN.json: {e}", flush=True)

        # ------------------------------------------------------------------
        # 1) AIDL (Phase 2 deterministic; plan provided)
        # ------------------------------------------------------------------
        print("[ARCHITECT] Step 1: Generate VHAL AIDL", flush=True)
        generate_vhal_aidl(spec, plan=plan)

        # ------------------------------------------------------------------
        # 2) C++ service (Phase 2 deterministic; plan provided)
        # ------------------------------------------------------------------
        print("[ARCHITECT] Step 2: Generate VHAL C++ service", flush=True)
        generate_vhal_service(spec, plan=plan)

        # ------------------------------------------------------------------
        # 3) Build glue (Soong + init rc + VINTF)
        # ------------------------------------------------------------------
        print("[ARCHITECT] Step 3: Generate build glue (Soong + init rc + VINTF)", flush=True)
        generate_vhal_aidl_bp()
        generate_vhal_service_build_glue()

        # ------------------------------------------------------------------
        # 4) Framework service generation (HVAC only)
        # ------------------------------------------------------------------
        print(f"[ARCHITECT] Step 4: Generate framework service (domain={domain})", flush=True)

        if domain == "HVAC":
            generate_car_service(spec)
        else:
            print(f"[ARCHITECT] Domain={domain}: skip framework generation (HVAC-only).", flush=True)

        # ------------------------------------------------------------------
        # 5) SELinux
        # ------------------------------------------------------------------
        print("[ARCHITECT] Step 5: Generate SELinux policy", flush=True)
        generate_selinux(spec)

        print("[ARCHITECT] ================================")
        print("[ARCHITECT] HAL GENERATION COMPLETED ✅")
        print("[ARCHITECT] ================================")
