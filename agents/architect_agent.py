import json
from pathlib import Path
from concurrent.futures import ThreadPoolExecutor, as_completed

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

        # Basic validation
        name_set = {p.id for p in spec.properties}
        if len(name_set) != len(spec.properties):
            print(f"[WARNING] Duplicate property names detected ({len(spec.properties) - len(name_set)} duplicates)")

        # ------------------------------------------------------------------
        # Step 0: Generate plan — everything else depends on this.
        # ------------------------------------------------------------------
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

        # ------------------------------------------------------------------
        # Steps 1–5: all independent of each other, run in parallel.
        #
        #   Step 1 — AIDL interface          (needs plan_text)
        #   Step 2 — C++ VehicleHalService   (needs plan_text)
        #   Step 3 — Build glue files        (no deps)
        #   Step 4 — Car framework service   (needs spec)
        #   Step 5 — SELinux policy          (needs spec)
        # ------------------------------------------------------------------
        print("[ARCHITECT] Steps 1–5: Running in parallel...")

        # Derive the Car service class name from the domain
        # e.g. HVAC → CarHvacService, POWERTRAIN → CarPowertrainService
        car_service_class = f"Car{domain.capitalize()}Service"

        # Draft root: AIDL + C++ write to .llm_draft/latest so PromoteDraftAgent
        # can validate before promoting to the final layout.
        # Build glue, car service, selinux write directly to output_root.
        draft_root = str(self.output_root / ".llm_draft" / "latest")
        out = str(self.output_root)

        def _step1_aidl():
            success = generate_vhal_aidl(plan_text, output_root=draft_root)
            label = "Success" if success else "Used fallback"
            print(f"  [AIDL] {label}")
            return ("AIDL", True)

        def _step2_service():
            success = generate_vhal_service(plan_text, output_root=draft_root)
            label = "Success" if success else "Used fallback"
            print(f"  [VHAL SERVICE] {label}")
            return ("VHAL SERVICE", True)

        def _step3_build():
            generate_vhal_aidl_bp(output_root=out)
            generate_vhal_service_build_glue(output_root=out)
            print("  [BUILD] Build glue generated")
            return ("BUILD", True)

        def _step4_car_service():
            generate_car_service(spec, output_root=out)
            print(f"  [CAR SERVICE] {car_service_class}.java generated")
            return ("CAR SERVICE", True)

        def _step5_selinux():
            generate_selinux(spec, output_root=out)
            print("  [SELINUX] Policy generated")
            return ("SELINUX", True)

        steps = [
            ("Step 1 — AIDL",           _step1_aidl),
            ("Step 2 — C++ Service",    _step2_service),
            ("Step 3 — Build glue",     _step3_build),
            ("Step 4 — Car Service",    _step4_car_service),
            ("Step 5 — SELinux",        _step5_selinux),
        ]

        with ThreadPoolExecutor(max_workers=len(steps)) as pool:
            futures = {pool.submit(fn): name for name, fn in steps}
            for future in as_completed(futures):
                step_name = futures[future]
                try:
                    future.result()
                except Exception as e:
                    print(f"  [ARCHITECT] {step_name} FAILED: {e}")

        print("[ARCHITECT] ===============================")
        print("[ARCHITECT] Module generation COMPLETE")
        print("[ARCHITECT] ===============================")