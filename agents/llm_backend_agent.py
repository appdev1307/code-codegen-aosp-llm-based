# agents/llm_backend_agent.py
from pathlib import Path
import json  # ← THIS WAS MISSING!
from llm_client import call_llm
from tools.safe_writer import SafeWriter

class LLMBackendAgent:
    def __init__(self, output_root="output"):
        self.writer = SafeWriter(output_root)
        self.backend_dir = Path(output_root) / "backend" / "vss_dynamic_server"  # ← Use Path from start

    def run(self, module_signal_map: dict, all_properties: list):
        print("[LLM BACKEND] Asking LLM to generate full FastAPI + WebSocket backend...")

        self.backend_dir.mkdir(parents=True, exist_ok=True)

        prop_text = "\n".join([f"- {getattr(p, 'property_id', 'UNKNOWN')} ({getattr(p, 'type', 'UNKNOWN')})" for p in all_properties])

        prompt = f"""
Generate a complete FastAPI backend with WebSocket for live vehicle telemetry.

Features:
- REST endpoint /api/data → current vehicle state (grouped by modules)
- WebSocket /ws/live → push updates every 1s
- Data structure grouped by modules: {list(module_signal_map.keys())}
- Properties: {prop_text}
- Include requirements.txt
- Use Pydantic models for structure
- Simulate realistic vehicle data (random values in range)

Output ONLY valid JSON:
{{
  "files": [
    {{"path": "main.py", "content": "..."}},
    {{"path": "requirements.txt", "content": "..."}}
  ]
}}

Generate the full backend now.
"""

        raw = call_llm(prompt, temperature=0.0, response_format="json")

        try:
            # Clean and parse JSON
            cleaned = raw.strip()
            if cleaned.startswith("```json"):
                cleaned = cleaned[7:]
            if cleaned.endswith("```"):
                cleaned = cleaned[:-3]
            cleaned = cleaned.strip()

            data = json.loads(cleaned)

            for file in data.get("files", []):
                rel_path = file.get("path", "").lstrip("/")
                content = file.get("content", "")
                if rel_path and content:
                    full_path = self.backend_dir / rel_path
                    full_path.parent.mkdir(parents=True, exist_ok=True)
                    self.writer.write(str(full_path), content.rstrip() + "\n")

            print(f"[LLM BACKEND] Full backend generated in {self.backend_dir}/")
            print("    → Run: cd backend/vss_dynamic_server && uvicorn main:app --reload")

        except Exception as e:
            print(f"[ERROR] Backend generation failed: {e}")
            # Save raw output for debug
            debug_path = self.backend_dir / "RAW_LLM_OUTPUT.txt"
            debug_path.parent.mkdir(parents=True, exist_ok=True)
            debug_path.write_text(raw)
            print(f"    → Raw output saved to {debug_path} for debugging")
