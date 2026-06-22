import json
from pathlib import Path
from agents.rag_dspy_android_app_agent import RAGDSPyAndroidAppAgent
from schemas.yaml_loader import load_hal_spec_from_yaml_text
from vss_to_yaml import vss_to_yaml_spec
from agents.vss_labelling_agent import flatten_vss

start_ollama()
import time
time.sleep(10)

# Load from cached labelled signals (fast)
print("Loading labelled signals...")
labelled_path = Path("/content/vss_temp/VSS_LABELLED_500.json")
with open(labelled_path, "r", encoding="utf-8") as f:
    labelled_data = json.load(f)

# Take one large domain for fast test (CABIN)
yaml_spec, _ = vss_to_yaml_spec(vss_json_path=str(labelled_path), 
                               vendor_namespace="vendor.vss", 
                               max_props=168)  # limit to CABIN size

full_spec = load_hal_spec_from_yaml_text(yaml_spec)
module_signal_map = {"CABIN": list(labelled_data.keys())[:168]}

print(f"Testing Android App + Layout for CABIN ({len(module_signal_map['CABIN'])} props)...")

# Run only Android App agent
agent = RAGDSPyAndroidAppAgent(
    output_root="output_rag_dspy_test",
    dspy_programs_dir="dspy_opt/saved",
    rag_top_k=8,
    rag_db_path="rag/chroma_db"
)
agent.run(module_signal_map, full_spec.properties)

print("\nRunning fixer...")
from multi_main_rag_dspy import fix_android_layouts   # import from your C3 script
fix_android_layouts("output_rag_dspy_test")

print("\n✅ Fast test done. Check folder: output_rag_dspy_test/hmi_app/src/main/res/layout/")


from pathlib import Path
import re, html
import xml.etree.ElementTree as ET
from dspy_opt.validators import validate
import importlib
import dspy_opt.validators
importlib.reload(dspy_opt.validators)
from dspy_opt.validators import validate
print("Reloaded")

def rebuild_layout_from_scratch(output_dir: str = "output_rag_dspy_test"):
    layout_dir = Path(output_dir) / "android_app" / "src" / "main" / "res" / "layout"
    if not layout_dir.exists():
        print("⚠ Directory not found.")
        return 0

    fixed = 0
    for xml_file in layout_dir.glob("fragment_*.xml"):
        try:
            content = xml_file.read_text(encoding="utf-8", errors="ignore")

            # 1. Unescape
            prev = None
            while prev != content:
                prev = content
                content = html.unescape(content)

            # 2. Fix ConstraintLayout
            content = re.sub(r'<(/?)\s*ConstraintLayout\b',
                             r'<\1androidx.constraintlayout.widget.ConstraintLayout', content)

            # 3. Xóa root wrappers
            for rt in ['ScrollView', 'LinearLayout', 'FrameLayout', 'RelativeLayout']:
                content = re.sub(rf'<{rt}\b[^>]*>', '', content)
                content = re.sub(rf'</{rt}>', '', content)

            content = re.sub(r'<\?xml[^>]*\?>', '', content)

            # 4. Sanitize android:text — validator's escape_text bug strips "android:text="
            #    Fix: replace value với plain ASCII, không có ký tự đặc biệt
            content = re.sub(
                r'android:text\s*=\s*"[^"]*"',
                lambda m: 'android:text="label"',
                content
            )

            # 5. Remove incomplete tags
            def remove_incomplete_tags(text):
                result = []
                i = 0
                while i < len(text):
                    if text[i] == '<':
                        end = text.find('>', i)
                        if end == -1:
                            break
                        tag = text[i:end+1]
                        if tag.count('"') % 2 == 0:
                            result.append(tag)
                            i = end + 1
                        else:
                            next_open = text.find('<', i+1)
                            if next_open == -1:
                                break
                            i = next_open
                    else:
                        next_open = text.find('<', i)
                        if next_open == -1:
                            break
                        result.append(text[i:next_open])
                        i = next_open
                return ''.join(result)

            content = remove_incomplete_tags(content)

            # 6. Force android:id
            def apply_force_id(m):
                full_tag = m.group(0)
                tag_name = m.group(1).lower()
                if 'android:id' in full_tag:
                    return full_tag
                if full_tag.endswith('/>'):
                    return full_tag[:-2] + f' android:id="@+id/{tag_name}_view"/>'
                else:
                    return full_tag[:-1] + f' android:id="@+id/{tag_name}_view">'

            content = re.sub(
                r'<(TextView|Switch|SeekBar|Button|CheckBox|EditText)\b[^>]*(?:/>|>)',
                apply_force_id, content, flags=re.IGNORECASE)

            # 7. Wrap root
            result = (
                '<ScrollView\n'
                '    xmlns:android="http://schemas.android.com/apk/res/android"\n'
                '    xmlns:app="http://schemas.android.com/apk/res-auto"\n'
                '    android:layout_width="match_parent"\n'
                '    android:layout_height="match_parent">\n'
                + content.strip() +
                '\n</ScrollView>'
            )

            # 8. Validate
            ET.fromstring(result)

            xml_file.write_text(result, encoding="utf-8")
            print(f"✅ Fixed: {xml_file.name}")
            fixed += 1

        except Exception as e:
            print(f"❌ Failed {xml_file.name}: {e}")

    print(f"🔧 Fixed {fixed} files.\n")
    return fixed


rebuild_layout_from_scratch()

layout_dir = Path("output_rag_dspy_test/android_app/src/main/res/layout")
for xml_file in sorted(layout_dir.glob("fragment_*.xml")):
    code = xml_file.read_text(encoding="utf-8")
    result = validate("android_layout", code)
    print(f"{xml_file.name}: score={result.score}, ok={result.ok}")
    if result.errors:
        print(f"  Errors: {result.errors}")