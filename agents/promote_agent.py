# FILE: agents/promote_agent.py

import json
import os
import re
import shutil
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

from tools.safe_writer import SafeWriter


@dataclass
class PromoteResult:
    ok: bool
    promoted: List[str]
    rejected: List[str]
    errors: List[str]
    warnings: List[str]
    draft_root: str
    report_path: str


class PromoteAgent:
    """
    Stage 2: Promote LLM draft outputs into the real 'output/' AOSP layout.

    Key properties:
    - NO rewriting of file content (copy-as-is).
    - Deterministic gating (allowlist + invariant checks).
    - If ANY hard gate fails => NO promotion.
    - Produces an auditable report in output/STAGE2_REPORT.json

    Expected draft layout:
      output/.llm_draft/<run_id>/hardware/interfaces/automotive/vehicle/aidl/...
      output/.llm_draft/<run_id>/hardware/interfaces/automotive/vehicle/impl/...
      output/.llm_draft/<run_id>/system/sepolicy/vendor/...
      ... plus init + vintf

    If you use a different draft folder, set DRAFT_ROOT via env.
    """

    def __init__(self, output_root: str = "output"):
        self.name = "Promote Agent"
        self.output_root = Path(output_root)
        self.writer = SafeWriter(str(self.output_root))

        # You can override this at runtime:
        #   DRAFT_ROOT=output/.llm_draft/latest python ...
        self.draft_root = Path(os.getenv("DRAFT_ROOT", str(self.output_root / ".llm_draft" / "latest")))

        # Hard allowlist (relative paths under draft_root)
        # If you add new artifacts, add them here explicitly.
        self.allowlist_prefixes = [
            "hardware/interfaces/automotive/vehicle/aidl/",
            "hardware/interfaces/automotive/vehicle/impl/",
            "system/sepolicy/vendor/",
        ]

        # Exact required files (minimal OEM-grade bringup set)
        self.required_files = [
            # AIDL core
            "hardware/interfaces/automotive/vehicle/aidl/android/hardware/automotive/vehicle/IVehicle.aidl",
            "hardware/interfaces/automotive/vehicle/aidl/android/hardware/automotive/vehicle/IVehicleCallback.aidl",
            "hardware/interfaces/automotive/vehicle/aidl/android/hardware/automotive/vehicle/VehiclePropValue.aidl",

            # AIDL Soong
            "hardware/interfaces/automotive/vehicle/aidl/Android.bp",

            # Service C++
            "hardware/interfaces/automotive/vehicle/impl/VehicleHalService.cpp",
            "hardware/interfaces/automotive/vehicle/impl/Android.bp",

            # init + vintf
            "hardware/interfaces/automotive/vehicle/impl/android.hardware.automotive.vehicle-service.rc",
            "hardware/interfaces/automotive/vehicle/impl/android.hardware.automotive.vehicle-service.xml",

            # sepolicy (vendor)
            "system/sepolicy/vendor/vehiclehal.te",
            "system/sepolicy/vendor/vehiclehal_service.te",
            "system/sepolicy/vendor/file_contexts",
        ]

        # Forbidden content patterns (hard)
        self.forbidden_substrings = [
            "com.example",
            "AndroidManifest.xml",
            "<manifest",  # app manifest
        ]

    # -------------------------
    # Public API
    # -------------------------
    def run(self) -> PromoteResult:
        print(f"[DEBUG] {self.name}: start", flush=True)
        errors: List[str] = []
        warnings: List[str] = []
        promoted: List[str] = []
        rejected: List[str] = []

        if not self.draft_root.exists():
            errors.append(f"Draft root not found: {self.draft_root}")
            return self._finish(False, promoted, rejected, errors, warnings)

        # Collect candidate files
        draft_files = self._collect_files(self.draft_root)
        if not draft_files:
            errors.append(f"No files found under draft root: {self.draft_root}")
            return self._finish(False, promoted, rejected, errors, warnings)

        # Gate 0: allowlist prefixes
        allow_ok, allow_rej, allow_err = self._gate_allowlist(draft_files)
        rejected.extend(allow_rej)
        errors.extend(allow_err)
        if not allow_ok:
            return self._finish(False, promoted, rejected, errors, warnings)

        # Gate 1: required files exist
        req_ok, req_err = self._gate_required_files_present(draft_files)
        errors.extend(req_err)
        if not req_ok:
            return self._finish(False, promoted, rejected, errors, warnings)

        # Gate 2: content invariants (AIDL / C++ / bp / init / vintf / sepolicy)
        inv_ok, inv_err, inv_warn = self._gate_invariants()
        errors.extend(inv_err)
        warnings.extend(inv_warn)
        if not inv_ok:
            return self._finish(False, promoted, rejected, errors, warnings)

        # If all gates pass => promote (copy-as-is)
        for rel in self.required_files:
            src = self.draft_root / rel
            dst_rel = rel  # same relative path inside output/
            self._promote_copy(src, dst_rel)
            promoted.append(dst_rel)

        return self._finish(True, promoted, rejected, errors, warnings)

    # -------------------------
    # Gates
    # -------------------------
    def _gate_allowlist(self, draft_files: List[str]) -> Tuple[bool, List[str], List[str]]:
        rejected: List[str] = []
        errors: List[str] = []

        for rel in draft_files:
            if not any(rel.startswith(pfx) for pfx in self.allowlist_prefixes):
                rejected.append(rel)

        if rejected:
            errors.append(
                "Draft contains files outside allowlisted prefixes. "
                "Refusing promotion to protect output/. "
                f"Examples: {rejected[:5]}"
            )
            return False, rejected, errors

        return True, rejected, errors

    def _gate_required_files_present(self, draft_files: List[str]) -> Tuple[bool, List[str]]:
        errors: List[str] = []
        s = set(draft_files)
        missing = [p for p in self.required_files if p not in s]
        if missing:
            errors.append(f"Missing required draft files: {missing}")
            return False, errors
        return True, errors

    def _gate_invariants(self) -> Tuple[bool, List[str], List[str]]:
        """
        Hard invariants:
        - AIDL package + signatures
        - Android.bp sanity checks (very lightweight but catches common failures)
        - init rc must register correct service name + class
        - VINTF must declare hal name + version + interface/instance
        - sepolicy must include minimal type declarations and contexts
        """
        errors: List[str] = []
        warnings: List[str] = []

        # Gate 2a: Forbidden content scan (hard)
        for rel in self.required_files:
            t = self._read_text(rel)
            low = t.lower()
            for s in self.forbidden_substrings:
                if s.lower() in low:
                    errors.append(f"Forbidden content '{s}' found in {rel}")
                    break

        # Gate 2b: AIDL checks (hard)
        errors.extend(self._check_aidl("hardware/interfaces/automotive/vehicle/aidl/android/hardware/automotive/vehicle/IVehicle.aidl"))
        errors.extend(self._check_aidl("hardware/interfaces/automotive/vehicle/aidl/android/hardware/automotive/vehicle/IVehicleCallback.aidl"))
        errors.extend(self._check_aidl("hardware/interfaces/automotive/vehicle/aidl/android/hardware/automotive/vehicle/VehiclePropValue.aidl", is_vehicle_prop_value=True))

        # Gate 2c: C++ checks (hard-ish)
        errors.extend(self._check_cpp("hardware/interfaces/automotive/vehicle/impl/VehicleHalService.cpp"))

        # Gate 2d: Android.bp checks (hard-ish)
        errors.extend(self._check_bp("hardware/interfaces/automotive/vehicle/aidl/Android.bp", expect_aidl_interface=True))
        errors.extend(self._check_bp("hardware/interfaces/automotive/vehicle/impl/Android.bp", expect_cc_binary=True))

        # Gate 2e: init rc checks (hard-ish)
        errors.extend(self._check_init_rc("hardware/interfaces/automotive/vehicle/impl/android.hardware.automotive.vehicle-service.rc"))

        # Gate 2f: VINTF checks (hard-ish)
        errors.extend(self._check_vintf("hardware/interfaces/automotive/vehicle/impl/android.hardware.automotive.vehicle-service.xml"))

        # Gate 2g: sepolicy checks (hard-ish)
        sp_err, sp_warn = self._check_sepolicy_bundle()
        errors.extend(sp_err)
        warnings.extend(sp_warn)

        return (len(errors) == 0), errors, warnings

    # -------------------------
    # Check helpers
    # -------------------------
    def _check_aidl(self, rel: str, is_vehicle_prop_value: bool = False) -> List[str]:
        errs: List[str] = []
        t = self._read_text(rel)

        if "package android.hardware.automotive.vehicle;" not in t:
            errs.append(f"{rel}: missing package android.hardware.automotive.vehicle;")

        if is_vehicle_prop_value:
            # Must define parcelable VehiclePropValue (can be empty)
            if re.search(r"\bparcelable\s+VehiclePropValue\s*;", t) is None:
                errs.append(f"{rel}: must declare 'parcelable VehiclePropValue;'")
            return errs

        if rel.endswith("IVehicle.aidl"):
            # Must contain exact method signatures (order can vary)
            required = [
                r"\bVehiclePropValue\s+get\s*\(\s*int\s+propId\s*,\s*int\s+areaId\s*\)\s*;",
                r"\bvoid\s+set\s*\(\s*in\s+VehiclePropValue\s+value\s*\)\s*;",
                r"\bvoid\s+registerCallback\s*\(\s*in\s+IVehicleCallback\s+callback\s*\)\s*;",
                r"\bvoid\s+unregisterCallback\s*\(\s*in\s+IVehicleCallback\s+callback\s*\)\s*;",
            ]
            for pat in required:
                if re.search(pat, t) is None:
                    errs.append(f"{rel}: missing required method signature matching: {pat}")

        if rel.endswith("IVehicleCallback.aidl"):
            if re.search(r"\bvoid\s+onPropertyEvent\s*\(\s*in\s+VehiclePropValue\s+value\s*\)\s*;", t) is None:
                errs.append(f"{rel}: missing required callback method onPropertyEvent(in VehiclePropValue value);")

        return errs

    def _check_cpp(self, rel: str) -> List[str]:
        errs: List[str] = []
        t = self._read_text(rel)

        must_have = [
            "BnIVehicle",
            "AServiceManager_addService",
            "android.hardware.automotive.vehicle.IVehicle/default",
            "registerCallback",
            "unregisterCallback",
        ]
        for s in must_have:
            if s not in t:
                errs.append(f"{rel}: missing required token: {s}")

        # Header expectations (common for AIDL NDK)
        if "<aidl/android/hardware/automotive/vehicle/BnIVehicle.h>" not in t:
            errs.append(f"{rel}: missing include <aidl/.../BnIVehicle.h>")

        return errs

    def _check_bp(self, rel: str, expect_aidl_interface: bool = False, expect_cc_binary: bool = False) -> List[str]:
        errs: List[str] = []
        t = self._read_text(rel)

        # Lightweight sanity checks; not a Soong parser
        if "{" not in t or "}" not in t:
            errs.append(f"{rel}: does not look like Blueprint (missing braces)")

        if expect_aidl_interface:
            if "aidl_interface" not in t:
                errs.append(f"{rel}: expected aidl_interface {{ ... }}")
            if "android.hardware.automotive.vehicle" not in t:
                errs.append(f"{rel}: expected name android.hardware.automotive.vehicle")
            # Encourage backend ndk or cpp (varies)
            if "backend" not in t:
                errs.append(f"{rel}: expected backend configuration for AIDL interface")

        if expect_cc_binary:
            if "cc_binary" not in t and "cc_defaults" not in t:
                errs.append(f"{rel}: expected cc_binary (service) module")
            if "VehicleHalService.cpp" not in t:
                errs.append(f"{rel}: expected to reference VehicleHalService.cpp in srcs")

        return errs

    def _check_init_rc(self, rel: str) -> List[str]:
        errs: List[str] = []
        t = self._read_text(rel)

        # Service name convention: many OEMs use android.hardware.automotive.vehicle-service
        if re.search(r"^\s*service\s+android\.hardware\.automotive\.vehicle-service\b", t, re.MULTILINE) is None:
            errs.append(f"{rel}: expected 'service android.hardware.automotive.vehicle-service ...'")

        # 'class hal' is typical
        if "class hal" not in t:
            errs.append(f"{rel}: expected 'class hal'")

        # Must start on boot
        if "oneshot" not in t and "disabled" in t:
            # Not always wrong, but risky. Mark as error only if obviously broken.
            errs.append(f"{rel}: appears disabled without oneshot; ensure service starts")

        return errs

    def _check_vintf(self, rel: str) -> List[str]:
        errs: List[str] = []
        t = self._read_text(rel)

        # Very lightweight VINTF checks
        # Expect HAL name and interface/instance
        if "<manifest" not in t or "</manifest>" not in t:
            errs.append(f"{rel}: not a manifest xml (missing <manifest> root)")

        # AIDL HAL name can appear as:
        #   <name>android.hardware.automotive.vehicle</name>
        # interface name "IVehicle" and instance "default"
        if "android.hardware.automotive.vehicle" not in t:
            errs.append(f"{rel}: expected hal name android.hardware.automotive.vehicle")

        if "<interface>" not in t or "IVehicle" not in t:
            errs.append(f"{rel}: expected interface IVehicle")

        if "default" not in t:
            errs.append(f"{rel}: expected instance 'default'")

        return errs

    def _check_sepolicy_bundle(self) -> Tuple[List[str], List[str]]:
        errs: List[str] = []
        warns: List[str] = []

        te1 = self._read_text("system/sepolicy/vendor/vehiclehal.te")
        te2 = self._read_text("system/sepolicy/vendor/vehiclehal_service.te")
        fc = self._read_text("system/sepolicy/vendor/file_contexts")

        # Minimal types (names can vary; but you need *something* coherent)
        if "type vehiclehal" not in te1 and "type vehicle_hal" not in te1:
            warns.append("system/sepolicy/vendor/vehiclehal.te: no obvious 'type vehiclehal' declaration (check naming)")

        if "vehiclehal_service" not in te2 and "vehicle_hal_service" not in te2:
            warns.append("system/sepolicy/vendor/vehiclehal_service.te: no obvious service domain type (check naming)")

        # File contexts must map your service binary path
        # Example: /vendor/bin/hw/android.hardware.automotive.vehicle-service u:object_r:hal_vehicle_default_exec:s0
        if "/vendor/bin" not in fc and "/system/bin" not in fc:
            errs.append("system/sepolicy/vendor/file_contexts: missing any /vendor/bin or /system/bin mapping; likely incomplete")

        return errs, warns

    # -------------------------
    # File helpers
    # -------------------------
    def _collect_files(self, root: Path) -> List[str]:
        out: List[str] = []
        for p in root.rglob("*"):
            if p.is_file():
                rel = str(p.relative_to(root)).replace("\\", "/")
                out.append(rel)
        return sorted(out)

    def _read_text(self, rel: str) -> str:
        p = self.draft_root / rel
        return p.read_text(encoding="utf-8", errors="replace")

    def _promote_copy(self, src: Path, dst_rel: str) -> None:
        # Ensure parent directory exists under output/
        dst_abs = self.output_root / dst_rel
        dst_abs.parent.mkdir(parents=True, exist_ok=True)

        # Copy byte-for-byte
        shutil.copyfile(src, dst_abs)

    def _finish(
        self,
        ok: bool,
        promoted: List[str],
        rejected: List[str],
        errors: List[str],
        warnings: List[str],
    ) -> PromoteResult:
        report = {
            "ok": ok,
            "draft_root": str(self.draft_root),
            "promoted": promoted,
            "rejected": rejected,
            "errors": errors,
            "warnings": warnings,
        }
        report_path = self.output_root / "STAGE2_REPORT.json"
        report_path.parent.mkdir(parents=True, exist_ok=True)
        report_path.write_text(json.dumps(report, indent=2) + "\n", encoding="utf-8")

        if ok:
            print(f"[DEBUG] {self.name}: promotion OK (files={len(promoted)})", flush=True)
        else:
            print(f"[WARN]  {self.name}: promotion FAILED (errors={len(errors)})", flush=True)

        return PromoteResult(
            ok=ok,
            promoted=promoted,
            rejected=rejected,
            errors=errors,
            warnings=warnings,
            draft_root=str(self.draft_root),
            report_path=str(report_path),
        )
