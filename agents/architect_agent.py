import json
from pathlib import Path
from typing import Optional

from agents.plan_agent import PlanAgent
from agents.vhal_aidl_agent import generate_vhal_aidl
from agents.vhal_service_agent import generate_vhal_service
from agents.vhal_aidl_build_agent import generate_vhal_aidl_bp
from agents.vhal_service_build_agent import generate_vhal_service_build_glue
from agents.car_service_agent import generate_car_service
from agents.selinux_agent import generate_selinux


class ArchitectAgent:
    def __init__(self, output_root: str = "output"):
        self.output_root = Path(output_root)
        self.output_root.mkdir(parents=True, exist_ok=True)

    def run(self, spec) -> None:
        print("[ARCHITECT] ===============================")
        print("[ARCHITECT] AAOS HAL Architect Agent START")
        print("[ARCHITECT] ===============================")

        # Domain handling
        raw_domain = getattr(spec, "domain", None)
        domain = (raw_domain or "").strip().upper() if isinstance(raw_domain, str) else "UNKNOWN"
        print(f"[ARCHITECT] Domain: {domain}")

        # Show input spec summary
        try:
            spec_text = spec.to_llm_spec()
            # Quick summary for logging
            prop_count = len(spec.properties)
            first_names = [p.id for p in spec.properties[:3]] if prop_count > 0 else []
            print(f"[ARCHITECT] Module has {prop_count} properties")
            if first_names:
                print(f"[ARCHITECT] First few property names: {', '.join(first_names)}")
        except Exception as e:
            spec_text = f"[ERROR] spec.to_llm_spec() failed: {e}"
            print(f"[ARCHITECT] Warning: {spec_text}")

        print("[ARCHITECT] Input spec summary:")
        print(spec_text[:800] + "..." if len(spec_text) > 800 else spec_text)

        # Optional: basic validation
        name_set = {p.id for p in spec.properties}
        if len(name_set) != len(spec.properties):
            print(f"[WARNING] Duplicate property names detected ({len(spec.properties) - len(name_set)} duplicates)")

        # Step 0: Generate and save PLAN.json
        print("[ARCHITECT] Step 0: Generating module plan...")
        plan_agent = PlanAgent()
        plan = plan_agent.run(spec)

        plan_path = self.output_root / "PLAN.json"
        plan_path.write_text(
            json.dumps(plan, indent=2, ensure_ascii=False),
            encoding="utf-8"
        )
        print(f"[ARCHITECT] Saved plan to {plan_path}")

        plan_text = json.dumps(plan, separators=(",", ":"))

        # Step 1: Generate AIDL
        print("[ARCHITECT] Step 1: Generating AIDL interface...")
        aidl_success = generate_vhal_aidl(plan_text)
        print("[AIDL] Success" if aidl_success else "[AIDL] Used fallback")

        # Step 2: Generate C++ service
        print("[ARCHITECT] Step 2: Generating C++ VehicleHalService...")
        service_success = generate_vhal_service(plan_text)
        print("[VHAL SERVICE] Success" if service_success else "[VHAL SERVICE] Used fallback")

        # Step 3: Build glue
        print("[ARCHITECT] Step 3: Generating build files (Android.bp, rc, VINTF)...")
        generate_vhal_aidl_bp()
        generate_vhal_service_build_glue()
        print("[BUILD] Build glue generated")

        # Step 4: Car service (HVAC only for now)
        print(f"[ARCHITECT] Step 4: Framework service (domain={domain})")
        if domain == "HVAC":
            generate_car_service(spec)
            print("[CAR SERVICE] CarHvacService.java generated")
        else:
            print("[ARCHITECT] Skipping Car service (only HVAC supported)")

        # Step 5: SELinux
        print("[ARCHITECT] Step 5: Generating SELinux policy...")
        generate_selinux(spec)
        print("[SELINUX] Policy generated")

        print("[ARCHITECT] ===============================")
        print("[ARCHITECT] Module generation COMPLETE")
        print("[ARCHITECT] ===============================")