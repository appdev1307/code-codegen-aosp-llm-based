# agents/llm_backend_agent.py
from pathlib import Path
from llm_client import call_llm
from tools.safe_writer import SafeWriter

class LLMBackendAgent:
    def __init__(self, output_root="output"):
        self.writer = SafeWriter(output_root)
        self.backend_dir = "backend/vss_dynamic_server"

    def run(self, module_signal_map: dict, all_properties: list):
        print("[LLM BACKEND] Asking LLM to generate full FastAPI + WebSocket backend...")

        Path(self.backend_dir).mkdir(parents=True, exist_ok=True)

        prop_text = "\n".join([f"- {getattr(p, 'property_id', 'UNKNOWN')} ({getattr(p, 'type', 'UNKNOWN')})" for p in all_properties])

        prompt = f"""
Generate a complete FastAPI backend with WebSocket for live vehicle telemetry.

Features:
- REST endpoint /api/data → current vehicle state
- WebSocket /ws/live → push updates every 1s
- Data structure grouped by modules: {list(module_signal_map.keys())}
- Properties: {prop_text}
- Include requirements.txt
- Use Pydantic models if possible
- Simulate data for now (random values)

Output ONLY JSON with all files.
"""

        raw = call_llm(prompt, temperature=0.0, response_format="json")

        try:
            data = json.loads(raw.strip().removeprefix("```json").removesuffix("```").strip())
            for file in data.get("files", []):
                path = file.get("path", "").replace("backend/", "")
                content = file.get("content", "")
                if path and content:
                    full_path = Path(self.backend_dir) / path
                    full_path.parent.mkdir(parents=True, exist_ok=True)
                    self.writer.write(str(full_path), content + "\n")
            print(f"[LLM BACKEND] Full backend generated in {self.backend_dir}/")
        except Exception as e:
            print(f"[ERROR] Backend generation failed: {e}")
            Path(self.backend_dir) / "RAW_OUTPUT.txt".write_text(raw)