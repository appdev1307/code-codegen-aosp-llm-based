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
    def __init__(self, output_root="output"):
        self.output_root = Path(output_root)
        self.output_root.mkdir(parents=True, exist_ok=True)

    def run(self, spec):
        print("[ARCHITECT] Starting generation for module")

        domain = (getattr(spec, "domain", "") or "UNKNOWN").strip().upper()
        print(f"[ARCHITECT] Domain: {domain}")

        # Generate plan
        plan_agent = PlanAgent()
        plan = plan_agent.run(spec)

        # Save PLAN.json inside output
        plan_path = self.output_root / "PLAN.json"
        plan_path.write_text(json.dumps(plan, indent=2, ensure_ascii=False))
        print(f"[ARCHITECT] Saved PLAN.json to {plan_path}")

        plan_text = json.dumps(plan, separators=(",", ":"))

        # Generate AIDL
        print("[ARCHITECT] Generating AIDL...")
        generate_vhal_aidl(plan_text)

        # Generate C++ service
        print("[ARCHITECT] Generating C++ service...")
        generate_vhal_service(plan_text)

        # Build glue
        print("[ARCHITECT] Generating build glue...")
        generate_vhal_aidl_bp()
        generate_vhal_service_build_glue()

        # Car service (if HVAC)
        if domain == "HVAC":
            print("[ARCHITECT] Generating CarHvacService...")
            generate_car_service(spec)

        # SELinux
        print("[ARCHITECT] Generating SELinux...")
        generate_selinux(spec)

        print("[ARCHITECT] Module generation complete")