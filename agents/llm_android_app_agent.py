from pathlib import Path
import json
from llm_client import call_llm
from tools.safe_writer import SafeWriter


class LLMAndroidAppAgent:
    def __init__(self, output_root="output"):
        self.writer = SafeWriter(output_root)
        self.app_dir = Path("packages/apps/VssDynamicApp")

    def run(self, module_signal_map: dict, all_properties: list):
        print("[LLM ANDROID APP] Generating dynamic Android app from Vehicle HAL properties...")

        self.app_dir.mkdir(parents=True, exist_ok=True)

        # Build rich property text using names (Fix 1 alignment)
        prop_text_lines = []
        seen_names = set()
        for prop in all_properties:
            name = getattr(prop, "name", getattr(prop, "id", "UNKNOWN"))
            typ = getattr(prop, "type", "UNKNOWN")
            access = getattr(prop, "access", "READ")
            areas = getattr(prop, "areas", [])
            areas_str = f", areas={', '.join(areas)}" if areas else ""
            prop_text_lines.append(f"- {name} ({typ}, {access}{areas_str})")

            if name in seen_names:
                print(f"[WARNING] Duplicate property name detected: {name}")
            seen_names.add(name)

        prop_text = "\n".join(prop_text_lines)

        # Build module summary with actual property names (helps LLM generate correct tabs/UI)
        module_lines = []
        for module_name, prop_names in sorted(module_signal_map.items()):
            count = len(prop_names)
            if count == 0:
                module_lines.append(f"- {module_name}: (empty)")
                continue
            first_few = prop_names[:4]  # show a few examples
            remaining = f" (+{count - 4} more)" if count > 4 else ""
            names_str = ", ".join(first_few) + remaining
            module_lines.append(f"- {module_name}: {count} properties ({names_str})")

        module_text = "\n".join(module_lines)

        prompt = f"""
You are an expert Android Automotive OS app developer.
Generate a complete, buildable AOSP Android app that uses the `android.car` API (CarPropertyManager) to read (and where possible write) ALL properties from the custom Vehicle HAL.

App requirements:
- Package name: com.android.vssdynamic.app
- Main screen: TabLayout + ViewPager2 (one tab per module)
- Modules and their properties:
{module_text}
- All known properties (name, type, access):
{prop_text}
- UI guidelines:
  - BOOLEAN → Switch (toggle) or TextView ("Enabled"/"Disabled")
  - INT / FLOAT → TextView showing current value (with units if known from name)
  - If READ_WRITE → add controls: Button (set), Slider (for INT/FLOAT ranges), EditText (for manual input)
  - Use CarPropertyManager.registerCallback() for live updates
  - Handle errors (property not available, permission denied)
- Include:
  - AndroidManifest.xml with required car permissions (<uses-permission android:name="android.car.permission.CAR_*" />)
  - Full Android.bp for AOSP build
  - All necessary Kotlin/Java files, layout XMLs (res/layout/*.xml), strings.xml if needed
  - Use modern Android practices (View Binding or Compose if preferred, but keep it simple)
- Do NOT hardcode property IDs as numbers — use string names from the list above when calling CarPropertyManager

Output ONLY valid JSON with all files:
{{
  "files": [
    {{"path": "packages/apps/VssDynamicApp/src/main/java/com/android/vssdynamic/app/MainActivity.kt", "content": "... code ..."}},
    {{"path": "packages/apps/VssDynamicApp/src/main/AndroidManifest.xml", "content": "... xml ..."}},
    {{"path": "packages/apps/VssDynamicApp/Android.bp", "content": "... bp ..."}},
    ...
  ]
}}

Generate the full app now. Use Kotlin if possible (preferred in modern AOSP apps).
"""

        print("[LLM ANDROID APP] Calling LLM to generate the app...")

        raw = call_llm(
            prompt=prompt,
            temperature=0.0,
            response_format="json"
        )

        try:
            # Robust JSON cleaning
            cleaned = raw.strip().removeprefix("```json").removesuffix("```").strip()
            data = json.loads(cleaned)

            saved_count = 0
            for entry in data.get("files", []):
                path_str = entry.get("path")
                content = entry.get("content", "")
                if not path_str or not content:
                    continue

                # Make path relative to app_dir (remove prefix if LLM includes it)
                rel_path = path_str.replace("packages/apps/VssDynamicApp/", "").lstrip("/")
                full_path = self.app_dir / rel_path
                full_path.parent.mkdir(parents=True, exist_ok=True)

                self.writer.write(str(full_path), content.rstrip() + "\n")
                saved_count += 1
                print(f"[LLM ANDROID APP] Wrote: {full_path}")

            print(f"[LLM ANDROID APP] Successfully generated {saved_count} files in {self.app_dir}/")
            print(" → Check Android.bp, MainActivity, layouts, and manifest")

        except json.JSONDecodeError as e:
            print(f"[ERROR] Invalid JSON from LLM: {e}")
            raw_path = self.app_dir / "RAW_LLM_OUTPUT.txt"
            raw_path.write_text(raw)
            print(f" → Raw output saved: {raw_path}")
        except Exception as e:
            print(f"[ERROR] App generation failed: {e}")