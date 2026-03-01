# agents/promote_draft_agent.py
from pathlib import Path
import shutil


class PromoteDraftAgent:
    def run(self, draft_root: str = "output/.llm_draft/latest",
            final_root: str = "output"):
        """
        Copy successful LLM drafts to final AOSP layout.

        Parameters
        ----------
        draft_root : str
            Root directory where the LLM draft was staged.
            Default "output/.llm_draft/latest" preserves C1/C2 behaviour.
            Pass e.g. "output_rag_dspy/.llm_draft/latest" for C3.
        final_root : str
            Root directory for the final promoted output.
            Default "output" preserves C1/C2 behaviour.
            Pass e.g. "output_rag_dspy" for C3.
        """
        print("[PROMOTE] Copying successful LLM drafts to final AOSP layout...")
        draft_path = Path(draft_root) / "hardware"
        final_path = Path(final_root) / "hardware"

        if not draft_path.exists():
            # Also try without the /hardware suffix — some agents stage differently
            draft_path_alt = Path(draft_root)
            if draft_path_alt.exists():
                draft_path = draft_path_alt
                final_path = Path(final_root)
                print(f"[PROMOTE] Using alt draft path: {draft_path}")
            else:
                print(f"[PROMOTE] No draft found at {draft_path} — nothing to promote")
                return

        # Copy entire hardware tree, overwriting final
        if final_path.exists():
            shutil.rmtree(final_path)
        shutil.copytree(draft_path, final_path)

        print("[PROMOTE] Draft promoted successfully!")
        print(f"   → Final files now in {final_root}/hardware/interfaces/automotive/vehicle/")