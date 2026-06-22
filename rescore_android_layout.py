"""
rescore_android_layout.py
─────────────────────────
Post-fix rescore for android_layout in C3 (rag_dspy) and optionally C4.
Run AFTER fix_android_layouts() has been applied to output directories.

Usage:
    python rescore_android_layout.py
"""

import json
import html
import re
import xml.etree.ElementTree as ET
from pathlib import Path

# ── Import validators (reload to get latest fix) ──────────────────
import importlib
import dspy_opt.validators
importlib.reload(dspy_opt.validators)
from dspy_opt.validators import validate
from dspy_opt.metrics import score_file


# ── Layout fixer (same logic as in pipeline) ──────────────────────
def fix_android_layouts(output_dir: str):
    layout_dir = Path(output_dir) / "android_app" / "src" / "main" / "res" / "layout"
    if not layout_dir.exists():
        layout_dir = Path(output_dir) / "hmi_app" / "src" / "main" / "res" / "layout"
    if not layout_dir.exists():
        print(f"⚠ No layout dir in {output_dir}")
        return 0

    fixed = 0
    for xml_file in layout_dir.glob("fragment_*.xml"):
        try:
            content = xml_file.read_text(encoding="utf-8", errors="ignore")

            prev = None
            while prev != content:
                prev = content
                content = html.unescape(content)

            content = re.sub(r'<(/?)\s*ConstraintLayout\b',
                             r'<\1androidx.constraintlayout.widget.ConstraintLayout', content)

            for rt in ['ScrollView', 'LinearLayout', 'FrameLayout', 'RelativeLayout']:
                content = re.sub(rf'<{rt}\b[^>]*>', '', content)
                content = re.sub(rf'</{rt}>', '', content)
            content = re.sub(r'<\?xml[^>]*\?>', '', content)
            content = re.sub(r'\s*android:text\s*=\s*"[^"]*"', '', content)

            def remove_incomplete_tags(text):
                result, i = [], 0
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
                            nxt = text.find('<', i+1)
                            if nxt == -1:
                                break
                            i = nxt
                    else:
                        nxt = text.find('<', i)
                        if nxt == -1:
                            break
                        result.append(text[i:nxt])
                        i = nxt
                return ''.join(result)

            content = remove_incomplete_tags(content)

            def apply_force_id(m):
                full_tag, tag_name = m.group(0), m.group(1).lower()
                if 'android:id' in full_tag:
                    return full_tag
                return (full_tag[:-2] + f' android:id="@+id/{tag_name}_view"/>'
                        if full_tag.endswith('/>')
                        else full_tag[:-1] + f' android:id="@+id/{tag_name}_view">')

            content = re.sub(
                r'<(TextView|Switch|SeekBar|Button|CheckBox|EditText)\b[^>]*(?:/>|>)',
                apply_force_id, content, flags=re.IGNORECASE)

            content = (
                '<ScrollView\n'
                '    xmlns:android="http://schemas.android.com/apk/res/android"\n'
                '    xmlns:app="http://schemas.android.com/apk/res-auto"\n'
                '    android:layout_width="match_parent"\n'
                '    android:layout_height="match_parent">\n'
                + content.strip() +
                '\n</ScrollView>'
            )

            ET.fromstring(content)
            xml_file.write_text(content, encoding="utf-8")
            print(f"  ✅ Fixed: {xml_file.name}")
            fixed += 1
        except Exception as e:
            print(f"  ❌ Failed {xml_file.name}: {e}")

    print(f"  🔧 Fixed {fixed} files.")
    return fixed


# ── Rescore layout files ──────────────────────────────────────────
def rescore_layout(output_dir: str) -> dict:
    layout_dir = Path(output_dir) / "android_app" / "src" / "main" / "res" / "layout"
    if not layout_dir.exists():
        layout_dir = Path(output_dir) / "hmi_app" / "src" / "main" / "res" / "layout"
    if not layout_dir.exists():
        return {}

    scores = []
    for xml_file in sorted(layout_dir.glob("fragment_*.xml")):
        code = xml_file.read_text(encoding="utf-8")
        s = score_file("android_layout", code)
        result = validate("android_layout", code)
        status = "✓" if result.ok else "✗"
        print(f"  [{status}] {xml_file.name}: score={s:.3f} syntax={result.score:.3f}")
        if result.errors:
            print(f"       ← {result.errors[0][:60]}")
        scores.append(s)

    avg = round(sum(scores) / len(scores), 4) if scores else 0.0
    print(f"  → avg android_layout score: {avg}")
    return {"android_layout": avg}


# ── Update results JSON ───────────────────────────────────────────
def update_results_json(json_path: str, fixed_scores: dict):
    path = Path(json_path)
    if not path.exists():
        print(f"⚠ Not found: {json_path}")
        return

    data = json.loads(path.read_text())

    # Update per_agent_avg_scores
    if "per_agent_avg_scores" in data:
        old = data["per_agent_avg_scores"].get("android_layout", "N/A")
        data["per_agent_avg_scores"].update(fixed_scores)
        print(f"  per_agent_avg_scores.android_layout: {old} → {fixed_scores.get('android_layout')}")

    # Update avg_metric_score
    all_scores = list(data["per_agent_avg_scores"].values())
    data["avg_metric_score"] = round(sum(all_scores) / len(all_scores), 4)
    print(f"  avg_metric_score → {data['avg_metric_score']}")

    # Update per_stage_metrics for android_app stage
    for m in data.get("per_stage_metrics", []):
        if m.get("stage") == "android_app" and "file_scores" in m:
            old_s = m["file_scores"].get("android_layout", "N/A")
            m["file_scores"].update(fixed_scores)
            m["metric_score"] = round(
                sum(m["file_scores"].values()) / len(m["file_scores"]), 4)
            print(f"  stage android_app file_scores.android_layout: {old_s} → {fixed_scores.get('android_layout')}")

    path.write_text(json.dumps(data, indent=2, ensure_ascii=False))
    print(f"  ✅ Saved: {json_path}")


# ── Main ──────────────────────────────────────────────────────────
TARGETS = [
    {
        "label":      "C3 (RAG+DSPy)",
        "output_dir": "output_rag_dspy",
        "json_path":  "experiments/results/rag_dspy.json",
    },
    {
        "label":      "C4 (Feedback)",
        "output_dir": "output_c4_feedback",
        "json_path":  "experiments/results/c4_feedback.json",
    },
]

for t in TARGETS:
    print(f"\n{'='*55}")
    print(f"  {t['label']} — {t['output_dir']}")
    print(f"{'='*55}")

    print("\n[1] Fixing layouts...")
    fix_android_layouts(t["output_dir"])

    print("\n[2] Rescoring...")
    fixed_scores = rescore_layout(t["output_dir"])

    print("\n[3] Updating results JSON...")
    update_results_json(t["json_path"], fixed_scores)

print("\nDone. Sleep well 🌙")