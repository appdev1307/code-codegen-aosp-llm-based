# agents/design_doc_agent.py
from pathlib import Path
from llm_client import call_llm
from tools.safe_writer import SafeWriter

class DesignDocAgent:
    def __init__(self, output_root="output"):
        self.writer = SafeWriter(output_root)
        self.doc_dir = "docs/design"

    def run(self, module_signal_map: dict, all_properties: list, full_spec_text: str):
        print("[DESIGN DOC] Asking LLM to generate full design documents (UML, diagrams, architecture)...")

        Path(self.doc_dir).mkdir(parents=True, exist_ok=True)

        module_text = "\n".join([f"- {m}: {len(module_signal_map[m])} properties" for m in module_signal_map])
        prop_count = len(all_properties)

        prompt = f"""
You are a senior automotive software architect.

Generate complete design documentation for an AI-generated Android Automotive OS Vehicle HAL system from VSS.

System overview:
- {prop_count} VSS properties grouped into {len(module_signal_map)} modules
- Modules: {module_text}
- Generated components: AIDL HAL interface, C++ NDK service, SELinux policy, Android client app, backend telemetry server

Generate:
1. System Architecture Diagram (PlantUML syntax)
2. Class Diagram for VehicleHalService (PlantUML)
3. Sequence Diagram for property get/set with callback (PlantUML)
4. Component Diagram (PlantUML)
5. High-level Design Document (Markdown) with sections: Overview, Architecture, Modules, Data Flow, Security

Output ONLY valid JSON:
{{
  "files": [
    {{"path": "docs/design/architecture.puml", "content": "@startuml ... @enduml"}},
    {{"path": "docs/design/class_diagram.puml", "content": "..."}},
    {{"path": "docs/design/sequence_diagram.puml", "content": "..."}},
    {{"path": "docs/design/component_diagram.puml", "content": "..."}},
    {{"path": "docs/design/DESIGN_DOCUMENT.md", "content": "# Design Document\\n..."}}
  ]
}}

Use proper PlantUML syntax. Make it beautiful and accurate.

Full spec context:
{full_spec_text[:4000]}  # Truncated for token limit

Generate now.
"""

        raw = call_llm(
            prompt=prompt,
            temperature=0.1,  # Slight creativity for diagrams
            response_format="json"
        )

        try:
            import json
            data = json.loads(raw.strip().removeprefix("```json").removesuffix("```").strip())
            for file in data.get("files", []):
                path = file.get("path")
                content = file.get("content", "")
                if path and content:
                    full_path = Path(self.doc_dir) / Path(path).name
                    self.writer.write(str(full_path), content + "\n")
            print(f"[DESIGN DOC] Full design documents generated in {self.doc_dir}/")
            print("    → Open .puml files in PlantUML viewer (e.g., plantuml.com)")
            print("    → DESIGN_DOCUMENT.md for full overview")
        except Exception as e:
            print(f"[ERROR] Design doc generation failed: {e}")
            Path(self.doc_dir) / "RAW_DESIGN_OUTPUT.txt".write_text(raw)
