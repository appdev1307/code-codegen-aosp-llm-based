# agents/promote_draft_agent.py
from pathlib import Path
import shutil

class PromoteDraftAgent:
    def run(self, draft_root="output/.llm_draft/latest", final_root="output"):
        print("[PROMOTE] Copying successful LLM drafts to final AOSP layout...")
        draft_path = Path(draft_root) / "hardware"
        final_path = Path(final_root) / "hardware"

        if not draft_path.exists():
            print("[PROMOTE] No draft found — nothing to promote")
            return

        # Copy entire hardware tree, overwriting final
        if final_path.exists():
            shutil.rmtree(final_path)
        shutil.copytree(draft_path, final_path)

        print("[PROMOTE] Draft promoted successfully!")
        print("   → Final files now in output/hardware/interfaces/automotive/vehicle/")