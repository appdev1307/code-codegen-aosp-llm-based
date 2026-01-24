# agents/llm_android_app_agent.py
from pathlib import Path
from llm_client import call_llm
from tools.safe_writer import SafeWriter

class LLMAndroidAppAgent:
    def __init__(self, output_root="output"):
        self.writer = SafeWriter(output_root)
        self.app_dir = "packages/apps/VssDynamicApp"

    def run(self, module_signal_map: dict, all_properties: list):
        print("[LLM ANDROID APP] Asking LLM to generate full dynamic Android app from HAL...")

        Path(self.app_dir).mkdir(parents=True, exist_ok=True)

        # Convert properties to text for LLM
        prop_text = ""
        for prop in all_properties:
            pid = getattr(prop, "property_id", "UNKNOWN")
            typ = getattr(prop, "type", "UNKNOWN")
            access = getattr(prop, "access", "READ")
            prop_text += f"- {pid} ({typ}, {access})\n"

        module_text = "\n".join([f"- {m} ({len(module_signal_map[m])} properties)" for m in module_signal_map])

        prompt = f"""
You are an expert Android Automotive OS app developer.

Generate a complete, buildable AOSP Android app that uses the android.car API to read and display ALL properties from the AI-generated Vehicle HAL.

Requirements:
- Package: com.android.vssdynamic.app
- Use TabLayout + ViewPager2 or Navigation for multiple screens (one tab per module)
- Modules: {module_text}
- Properties: {prop_text}
- For BOOLEAN: use Switch or TextView with on/off
- For INT/FLOAT: use TextView with value
- For STRING: use TextView
- If READ_WRITE: add button/slider to write value
- Use CarPropertyManager to read properties
- Include AndroidManifest.xml with car permissions
- Include Android.bp for AOSP build
- Generate all necessary Java/Kotlin files, layout XML, manifest, build file

Output ONLY valid JSON:
{{
  "files": [
    {{"path": "packages/apps/VssDynamicApp/src/main/java/com/android/vssdynamic/app/MainActivity.java", "content": "..."}}
  ]
}}

Generate the full app now.
"""

        raw = call_llm(
            prompt=prompt,
            temperature=0.0,
            response_format="json"
        )

        # Parse and write files
        import json
        try:
            data = json.loads(raw.strip().removeprefix("```json").removesuffix("```").strip())
            for file in data.get("files", []):
                path = file.get("path")
                content = file.get("content", "")
                if path and content:
                    full_path = Path(self.app_dir) / path.replace("packages/apps/VssDynamicApp/", "")
                    full_path.parent.mkdir(parents=True, exist_ok=True)
                    self.writer.write(str(full_path), content + "\n")
            print(f"[LLM ANDROID APP] Full dynamic app generated in {self.app_dir}/")
        except Exception as e:
            print(f"[ERROR] LLM app generation failed: {e}")
            print("Raw output saved for debug")
            Path(self.app_dir) / "RAW_LLM_OUTPUT.txt".write_text(raw)