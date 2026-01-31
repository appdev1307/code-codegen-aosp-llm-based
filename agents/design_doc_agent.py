from pathlib import Path
import json
from llm_client import call_llm
from tools.safe_writer import SafeWriter


class DesignDocAgent:
    def __init__(self, output_root="output"):
        self.writer = SafeWriter(output_root)
        self.doc_dir = Path("docs/design")
        self.doc_dir.mkdir(parents=True, exist_ok=True)

    def run(self, module_signal_map: dict, all_properties: list, full_spec_text: str):
        print("[DESIGN DOC] Generating full design documents (UML, diagrams, architecture)...")

        # Build richer module summary using property names (Fix 1 alignment)
        module_summary_lines = []
        for module_name, prop_names in sorted(module_signal_map.items()):
            count = len(prop_names)
            if count == 0:
                module_summary_lines.append(f"- {module_name}: (empty)")
                continue
            first_few = prop_names[:3]
            remaining = f" (+{count-3} more)" if count > 3 else ""
            names_str = ", ".join(first_few) + remaining
            module_summary_lines.append(f"- {module_name}: {count} properties ({names_str})")

        module_text = "\n".join(module_summary_lines)
        prop_count = len(all_properties)

        # Optional: warn if names look inconsistent
        all_names = set()
        for names in module_signal_map.values():
            all_names.update(names)
        if len(all_names) < sum(len(v) for v in module_signal_map.values()):
            print("[DESIGN DOC] Warning: some property names appear in multiple modules")

        prompt = f"""
You are a senior automotive software architect specializing in Android Automotive OS Vehicle HAL.
Generate complete, professional design documentation for an AI-generated VSS → AAOS Vehicle HAL system.

System overview:
- Total VSS-derived properties: {prop_count}
- Grouped into {len(module_signal_map)} logical modules
- Modules and example properties:
{module_text}

Generated components include:
- AIDL HAL interface
- C++ NDK VehicleHalService implementation
- SELinux policy fragments
- Android client app (Java/Kotlin)
- Backend telemetry/processing server

Generate the following outputs:
1. System Architecture Diagram (PlantUML @startuml ... @enduml)
2. Class Diagram for VehicleHalService and related classes (PlantUML)
3. Sequence Diagram: property get/set flow with callback registration (PlantUML)
4. Component Diagram showing HAL, service, framework, SELinux, app (PlantUML)
5. High-level Design Document (Markdown) with sections:
   - Overview
   - Architecture
   - Modules & Properties
   - Data Flow & Callbacks
   - Security Considerations (SELinux, access control)

Output ONLY valid JSON:
{{
  "files": [
    {{"path": "docs/design/architecture.puml", "content": "@startuml\\n...\\n@enduml"}},
    {{"path": "docs/design/class_diagram.puml", "content": "..."}},
    {{"path": "docs/design/sequence_diagram.puml", "content": "..."}},
    {{"path": "docs/design/component_diagram.puml", "content": "..."}},
    {{"path": "docs/design/DESIGN_DOCUMENT.md", "content": "# Vehicle HAL Design Document\\n..."}}  
  ]
}}

Rules:
- Use correct PlantUML syntax (no syntax errors)
- Make diagrams clear, readable, and professional
- Reference property names from the module summary when relevant
- Keep diagrams concise but informative

Full spec context (truncated):
{full_spec_text[:4000]}

Generate now. Output only the JSON object.
"""

        print("[DESIGN DOC] Calling LLM for design documents...")

        raw = call_llm(
            prompt=prompt,
            temperature=0.1,  # slight creativity for diagram layout
            response_format="json"
        )

        try:
            # Robust JSON cleaning (handles common LLM wrappers)
            cleaned = raw.strip().removeprefix("```json").removesuffix("```").strip()
            data = json.loads(cleaned)

            saved_count = 0
            for entry in data.get("files", []):
                path_str = entry.get("path")
                content = entry.get("content", "")
                if not path_str or not content:
                    continue

                # Use only the filename part to avoid path injection issues
                safe_filename = Path(path_str).name
                full_path = self.doc_dir / safe_filename

                self.writer.write(str(full_path), content.rstrip() + "\n")
                saved_count += 1
                print(f"[DESIGN DOC] Wrote: {full_path}")

            print(f"[DESIGN DOC] Successfully generated {saved_count} design files in {self.doc_dir}/")
            print(" → Use PlantUML viewer (e.g. plantuml.com or VS Code plugin) for .puml files")
            print(" → DESIGN_DOCUMENT.md contains the full textual overview")

        except json.JSONDecodeError as e:
            print(f"[ERROR] Invalid JSON from LLM: {e}")
            raw_path = self.doc_dir / "RAW_LLM_OUTPUT.txt"
            raw_path.write_text(raw)
            print(f" → Raw LLM output saved to {raw_path}")
        except Exception as e:
            print(f"[ERROR] Design doc generation failed: {e}")