# agents/vhal_aidl_agent.py
from __future__ import annotations
import json
import re
import yaml
from pathlib import Path
from typing import Optional, Dict, Any, Union

from llm_client import call_llm
from tools.safe_writer import SafeWriter
from tools.json_contract import parse_json_object


class VHALAidlAgent:
    # Maximum VSS properties sent per LLM call.
    # 50 props in one prompt causes 1200s timeouts on 32B models.
    # 15 props -> ~300 output tokens -> ~30-60s per call.
    CHUNK_SIZE = 15

    def __init__(self, output_root: str = "output/.llm_draft/latest"):
        self.name = "VHAL AIDL Agent (VSS-aware)"
        self.output_root = output_root
        self.writer = SafeWriter(self.output_root)
        self.raw_dir = Path(self.output_root)
        self.raw_dir.mkdir(parents=True, exist_ok=True)
        self.report_path = Path(self.output_root) / "VHAL_AIDL_VALIDATE_REPORT.json"

        self.system_prompt = (
            "You are an expert Android Automotive OS (AAOS) Vehicle HAL engineer.\n"
            "Generate correct, production-grade AIDL files from the provided VSS spec.\n"
            "You MUST output ONLY valid JSON. No explanations, no markdown, no code blocks.\n"
            'If you cannot produce perfect JSON, output exactly: {"files": []}'
        )

        self.base_dir = "hardware/interfaces/automotive/vehicle/aidl"
        self.pkg_dir = f"{self.base_dir}/android/hardware/automotive/vehicle"

        self.required_files = [
            f"{self.pkg_dir}/IVehicle.aidl",
            f"{self.pkg_dir}/IVehicleCallback.aidl",
            f"{self.pkg_dir}/VehiclePropValue.aidl",
            f"{self.pkg_dir}/VehiclePropertyVss.aidl",
        ]

    def _parse_properties(self, plan_text: str):
        try:
            if plan_text.strip().startswith("spec_version"):
                spec = yaml.safe_load(plan_text)
            else:
                spec = json.loads(plan_text)
            return spec.get("properties", [])
        except Exception:
            return []

    def build_prompt(self, plan_text: str) -> str:
        props = self._parse_properties(plan_text)
        prop_lines = [
            f"- {p.get('name')} ({p.get('type')}, {p.get('access')})"
            for p in props
        ]
        required_list = "\n".join(f"- {f}" for f in self.required_files)

        chunk_note = ""
        try:
            data = json.loads(plan_text)
            info = data.get("_chunk_info")
            if info:
                chunk_note = (
                    f"\nNOTE: This is chunk {info} of a large spec. "
                    "Generate IVehicle/IVehicleCallback/VehiclePropValue only in chunk 1/N. "
                    "Always generate VehiclePropertyVss.aidl with THIS chunk's properties.\n"
                )
        except Exception:
            pass

        first_name = props[0].get("name", "PROP_0") if props else "PROP_0"
        second_name = props[1].get("name", "PROP_1") if len(props) > 1 else "PROP_1"
        props_text = "\n".join(prop_lines)

        return (
            "Generate complete Vehicle HAL AIDL files including vendor-specific property enum."
            f"{chunk_note}\n"
            "MANDATORY OUTPUT FORMAT:\n"
            "Return ONLY valid JSON:\n"
            '{\n'
            '  "files": [\n'
            '    {"path": "...", "content": "full file content with \\n for newlines"}\n'
            '  ]\n'
            '}\n\n'
            "CRITICAL RULES:\n"
            "- Output ONLY the JSON object. No extra text, no fences, no comments.\n"
            "- Use exact file paths listed below.\n"
            '- Escape properly: newlines as \\n, quotes as \\"\n\n'
            f"REQUIRED FILES (generate ALL of them):\n{required_list}\n\n"
            "AIDL REQUIREMENTS:\n"
            "- IVehicle.aidl: standard interface (get, set, registerCallback, unregisterCallback)\n"
            "- IVehicleCallback.aidl: onPropertyEvent(in VehiclePropValue)\n"
            "- VehiclePropValue.aidl: parcelable with all standard fields\n"
            "- VehiclePropertyVss.aidl: MUST contain @Backing(type=\"int\") enum with ALL spec properties\n\n"
            f"VSS PROPERTIES TO INCLUDE IN VehiclePropertyVss.aidl:\n{props_text}\n\n"
            "VehiclePropertyVss.aidl example structure:\n"
            "@VintfStability\n"
            '@Backing(type="int")\n'
            "enum VehiclePropertyVss {\n"
            f"    {first_name} = 0xF0000000,\n"
            f"    {second_name} = 0xF0000001,\n"
            "    // ... continue sequentially\n"
            "}\n\n"
            f"FULL VSS SPEC:\n{plan_text}\n\n"
            "OUTPUT ONLY THE JSON NOW:"
        )

    # ── Chunked entry point ───────────────────────────────────────────────────

    def run(self, plan_text: str) -> bool:
        print(f"[DEBUG] {self.name}: start")
        props = self._parse_properties(plan_text)

        if not props or len(props) <= self.CHUNK_SIZE:
            return self._run_single(plan_text, "attempt1")

        # Large spec: split into CHUNK_SIZE-property batches.
        # Structural files come from chunk 0; VehiclePropertyVss is merged.
        print(
            f"[DEBUG] {self.name}: {len(props)} properties -> "
            f"chunking into batches of {self.CHUNK_SIZE}"
        )
        chunks = [
            props[i:i + self.CHUNK_SIZE]
            for i in range(0, len(props), self.CHUNK_SIZE)
        ]

        all_entries: list = []
        structural_written = False

        for idx, chunk in enumerate(chunks):
            chunk_plan = self._make_chunk_plan(plan_text, chunk, idx, len(chunks))
            raw = call_llm(
                prompt=self.build_prompt(chunk_plan),
                system=self.system_prompt,
                temperature=0.0,
                response_format="json",
            )
            self._dump_raw(raw, f"chunk{idx + 1}")
            entries = self._extract_enum_entries(raw)
            all_entries.extend(entries)
            print(
                f"[DEBUG] {self.name}: chunk {idx + 1}/{len(chunks)} "
                f"-> {len(entries)} enum entries"
            )
            if idx == 0:
                structural_written = self._try_write_structural(raw)

        vss_ok = self._write_merged_vss_enum(all_entries)

        if not structural_written:
            print(f"[WARN] {self.name}: structural files missing -> repair pass")
            self._run_single(plan_text, "repair_structural")
        if not vss_ok:
            print(f"[WARN] {self.name}: no enum entries -> deterministic VSS fallback")
            self._write_fallback_vss(props)

        success = structural_written and vss_ok
        label = "success" if success else "partial/fallback"
        print(f"[DEBUG] {self.name}: done (chunked {label})")
        return success

    # ── Single-shot helper ────────────────────────────────────────────────────

    def _run_single(self, plan_text: str, label: str) -> bool:
        prompt = self.build_prompt(plan_text)
        raw = call_llm(
            prompt=prompt,
            system=self.system_prompt,
            temperature=0.0,
            response_format="json",
        )
        self._dump_raw(raw, label)
        if self._try_write_from_output(raw):
            print(f"[DEBUG] {self.name}: done ({label} success)")
            return True

        repair_prompt = (
            prompt
            + "\n\nPREVIOUS OUTPUT WAS INVALID OR INCOMPLETE.\n"
            "You MUST output valid JSON with ALL four required files and correct paths.\n"
            "Fix all issues. No explanations."
        )
        raw2 = call_llm(
            prompt=repair_prompt,
            system=self.system_prompt,
            temperature=0.0,
            response_format="json",
        )
        self._dump_raw(raw2, label + "_repair")
        if self._try_write_from_output(raw2):
            print(f"[DEBUG] {self.name}: done ({label} repair success)")
            return True

        print(f"[WARN] {self.name}: LLM failed both attempts -> deterministic fallback")
        self._write_fallback()
        return False

    # ── Chunk helpers ─────────────────────────────────────────────────────────

    def _make_chunk_plan(self, plan_text: str, chunk: list, idx: int, total: int) -> str:
        try:
            if plan_text.strip().startswith("spec_version"):
                base = yaml.safe_load(plan_text)
            else:
                base = json.loads(plan_text)
        except Exception:
            base = {}
        base = dict(base)
        base["properties"] = chunk
        base["_chunk_info"] = f"{idx + 1}/{total}"
        return json.dumps(base, separators=(",", ":"))

    def _extract_enum_entries(self, raw: str) -> list:
        """Return the body lines of VehiclePropertyVss enum from LLM JSON output."""
        try:
            data, _ = parse_json_object(raw.strip())
            if not data:
                return []
            for item in data.get("files", []):
                path = (item.get("path") or "").strip()
                if "VehiclePropertyVss" in path:
                    content = item.get("content", "")
                    in_enum = False
                    entries = []
                    for line in content.splitlines():
                        if "{" in line and not in_enum:
                            in_enum = True
                            continue
                        if in_enum and "}" in line:
                            break
                        if in_enum and line.strip():
                            entries.append(line)
                    return entries
        except Exception:
            pass
        return []

    def _write_merged_vss_enum(self, entries: list) -> bool:
        """Write VehiclePropertyVss.aidl from merged entries, reassigning sequential IDs."""
        if not entries:
            return False
        clean = []
        hex_idx = 0
        for line in entries:
            stripped = line.strip().rstrip(",")
            if not stripped or stripped.startswith("//"):
                continue
            name = stripped.split("=")[0].strip()
            if name:
                clean.append(f"    {name} = 0xF{hex_idx:07X},")
                hex_idx += 1
        if not clean:
            return False
        body = "\n".join(clean)
        content = (
            "package android.hardware.automotive.vehicle;\n\n"
            "@VintfStability\n"
            "@Backing(type=\"int\")\n"
            "enum VehiclePropertyVss {\n"
            f"{body}\n"
            "}\n"
        )
        self.writer.write(f"{self.pkg_dir}/VehiclePropertyVss.aidl", content)
        return True

    def _write_fallback_vss(self, props: list) -> None:
        """Write a deterministic VehiclePropertyVss.aidl from the raw property list."""
        lines = []
        for i, p in enumerate(props):
            name = p.get("name", f"PROP_{i}") if isinstance(p, dict) else f"PROP_{i}"
            lines.append(f"    {name} = 0xF{i:07X},")
        body = "\n".join(lines)
        content = (
            "package android.hardware.automotive.vehicle;\n\n"
            "@VintfStability\n"
            "@Backing(type=\"int\")\n"
            "enum VehiclePropertyVss {\n"
            f"{body}\n"
            "}\n"
        )
        self.writer.write(f"{self.pkg_dir}/VehiclePropertyVss.aidl", content)

    def _try_write_structural(self, raw: str) -> bool:
        """Write only the 3 structural AIDL files (not VehiclePropertyVss)."""
        structural = [f for f in self.required_files if "VehiclePropertyVss" not in f]
        try:
            data, _ = parse_json_object(raw.strip())
            if not data:
                return False
            written = []
            for item in data.get("files", []):
                path = self._sanitize_path((item.get("path") or "").strip())
                content = item.get("content")
                if path and path in structural and isinstance(content, str):
                    self.writer.write(path, content.rstrip() + "\n")
                    written.append(path)
            return all(p in written for p in structural)
        except Exception:
            return False

    # ── Original helpers (unchanged) ─────────────────────────────────────────

    def _try_write_from_output(self, text: str) -> bool:
        if not text.strip():
            return False

        data, err = parse_json_object(text.strip())
        report = {
            "parse_error": err,
            "valid_json": data is not None,
            "files_found": 0,
            "paths": [],
            "missing_required": self.required_files[:],
        }

        if not data or "files" not in data or not isinstance(data["files"], list):
            self.report_path.write_text(json.dumps(report, indent=2), encoding="utf-8")
            return False

        files = data["files"]
        report["files_found"] = len(files)
        written_paths = []

        for item in files:
            if not isinstance(item, dict):
                continue
            path = item.get("path", "").strip()
            content = item.get("content")
            if not path or not isinstance(content, str):
                continue
            safe_path = self._sanitize_path(path)
            if safe_path not in self.required_files:
                continue
            content = content.rstrip() + "\n"
            self.writer.write(safe_path, content)
            written_paths.append(safe_path)

        missing = [p for p in self.required_files if p not in written_paths]
        report["paths"] = written_paths
        report["missing_required"] = missing
        self.report_path.write_text(json.dumps(report, indent=2), encoding="utf-8")
        return len(missing) == 0

    def _dump_raw(self, text: str, label: str) -> None:
        (self.raw_dir / f"VHAL_AIDL_RAW_{label}.txt").write_text(
            text or "[EMPTY]", encoding="utf-8"
        )

    def _sanitize_path(self, rel_path: str) -> Optional[str]:
        if not rel_path:
            return None
        p = rel_path.replace("\\", "/").strip("/")
        p = re.sub(r"/+", "/", p)
        if ".." in p.split("/") or not p.startswith(
            "hardware/interfaces/automotive/vehicle/aidl/"
        ):
            return None
        return p

    def _write_fallback(self) -> None:
        iv = (
            "package android.hardware.automotive.vehicle;\n\n"
            "import android.hardware.automotive.vehicle.VehiclePropValue;\n"
            "import android.hardware.automotive.vehicle.IVehicleCallback;\n\n"
            "interface IVehicle {\n"
            "    VehiclePropValue get(int propId, int areaId);\n"
            "    void set(in VehiclePropValue value);\n"
            "    void registerCallback(in IVehicleCallback callback);\n"
            "    void unregisterCallback(in IVehicleCallback callback);\n"
            "}\n"
        )
        cb = (
            "package android.hardware.automotive.vehicle;\n\n"
            "import android.hardware.automotive.vehicle.VehiclePropValue;\n\n"
            "interface IVehicleCallback {\n"
            "    void onPropertyEvent(in VehiclePropValue value);\n"
            "}\n"
        )
        vp = (
            "package android.hardware.automotive.vehicle;\n\n"
            "parcelable VehiclePropValue {\n"
            "    int prop;\n"
            "    int areaId;\n"
            "    long timestamp;\n"
            "    int[] intValues;\n"
            "    float[] floatValues;\n"
            "    boolean[] boolValues;\n"
            "    String stringValue;\n"
            "}\n"
        )
        vss = (
            "package android.hardware.automotive.vehicle;\n\n"
            "@VintfStability\n"
            "@Backing(type=\"int\")\n"
            "enum VehiclePropertyVss {\n"
            "    VEHICLE_CHILDREN_ADAS_CHILDREN_ABS_CHILDREN_ISENABLED = 0xF0000000,\n"
            "    VEHICLE_CHILDREN_ADAS_CHILDREN_ABS_CHILDREN_ISENGAGED = 0xF0000001,\n"
            "}\n"
        )
        for path, content in [
            (f"{self.pkg_dir}/IVehicle.aidl", iv),
            (f"{self.pkg_dir}/IVehicleCallback.aidl", cb),
            (f"{self.pkg_dir}/VehiclePropValue.aidl", vp),
            (f"{self.pkg_dir}/VehiclePropertyVss.aidl", vss),
        ]:
            self.writer.write(path, content)


def generate_vhal_aidl(
    plan_or_spec: Union[str, Dict[str, Any], Any],
    output_root: str = "output/.llm_draft/latest",
) -> bool:
    if isinstance(plan_or_spec, str):
        plan_text = plan_or_spec
    elif isinstance(plan_or_spec, dict):
        plan_text = json.dumps(plan_or_spec, separators=(",", ":"))
    else:
        try:
            plan_text = plan_or_spec.to_llm_spec()
        except Exception:
            plan_text = "{}"

    return VHALAidlAgent(output_root=output_root).run(plan_text)
