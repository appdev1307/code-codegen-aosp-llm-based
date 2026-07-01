#!/usr/bin/env python3
"""
Merge custom VehicleProperty*.aidl into AOSP VehicleProperty.aidl
Target: aidl_property/ only (that's where AOSP build system reads it from)
"""
import os
import glob
import re
import sys
import shutil

if len(sys.argv) != 2:
    print("Usage: python3 merge_vehicle_property.py <custom_aidl_directory>")
    print("  Example: python3 merge_vehicle_property.py ~/output_c4_minimal/hardware/interfaces/automotive/vehicle/aidl/android/hardware/automotive/vehicle/")
    sys.exit(1)

CUSTOM_DIR = sys.argv[1]
if not os.path.isdir(CUSTOM_DIR):
    print("❌ Directory not found:", CUSTOM_DIR)
    sys.exit(1)

# ── Target: aidl_property only ───────────────────────────────────────────────
AOSP_BASE = os.path.expanduser(
    "~/aosp-14-auto/hardware/interfaces/automotive/vehicle"
)
TARGET_FILE = os.path.join(
    AOSP_BASE,
    "aidl_property/android/hardware/automotive/vehicle/VehicleProperty.aidl"
)

# ── Custom files ──────────────────────────────────────────────────────────────
EXCLUDE = {
    "VehicleProperty.aidl",
    "VehiclePropertyAccess.aidl",
    "VehiclePropertyStatus.aidl",
    "VehiclePropertyChangeMode.aidl",
}

custom_files = [
    f for f in sorted(glob.glob(os.path.join(CUSTOM_DIR, "VehicleProperty*.aidl")))
    if os.path.basename(f) not in EXCLUDE
]

if not custom_files:
    print("❌ No custom VehicleProperty*.aidl files found in:", CUSTOM_DIR)
    sys.exit(1)

print(f"🔄 Merging {len(custom_files)} custom file(s)")
print(f"   Source : {CUSTOM_DIR}")
print(f"   Target : {TARGET_FILE}")
print()

# ── Target must exist ─────────────────────────────────────────────────────────
if not os.path.exists(TARGET_FILE):
    print("❌ Target not found:", TARGET_FILE)
    print("   Restore from git:")
    print("   cd ~/aosp-14-auto/hardware/interfaces")
    print("   git checkout HEAD -- automotive/vehicle/aidl_property/android/hardware/automotive/vehicle/VehicleProperty.aidl")
    sys.exit(1)

with open(TARGET_FILE, "r", encoding="utf-8") as f:
    content = f.read()

# ── Imports must be intact ────────────────────────────────────────────────────
for required in ["VehicleArea", "VehiclePropertyGroup", "VehiclePropertyType"]:
    if f"import android.hardware.automotive.vehicle.{required}" not in content:
        print(f"❌ ABORT: import {required} missing — file may be corrupted.")
        print("   Restore from git first.")
        sys.exit(1)

# ── Idempotency guard ─────────────────────────────────────────────────────────
if "VSS Custom Properties" in content:
    print("⚠️  Already merged. Restore from git first to avoid duplicates:")
    print("   cd ~/aosp-14-auto/hardware/interfaces")
    print("   git checkout HEAD -- automotive/vehicle/aidl_property/android/hardware/automotive/vehicle/VehicleProperty.aidl")
    sys.exit(1)

# ── Find last } (end of enum) ─────────────────────────────────────────────────
last_brace_idx = content.rfind("}")
if last_brace_idx == -1:
    print("❌ Cannot find closing } in VehicleProperty.aidl")
    sys.exit(1)

before_close = content[:last_brace_idx]
after_close  = content[last_brace_idx:]

# ── Build custom block ────────────────────────────────────────────────────────
custom_block  = "\n    // ================================================================\n"
custom_block += "    // VSS Custom Properties (vendor, pre-encoded int32)\n"
custom_block += "    // ================================================================\n\n"

total_added = 0
prop_re = re.compile(r'^\s*[A-Z][A-Z0-9_]+\s*=\s*0x[0-9a-fA-F]+')

for filepath in custom_files:
    filename = os.path.basename(filepath)
    print(f"📄 {filename}")
    count   = 0
    in_enum = False

    with open(filepath, encoding="utf-8") as src:
        for line in src:
            s = line.strip()
            if re.match(r'^enum\s+\w+\s*\{', s):
                in_enum = True
                continue
            if not in_enum:
                continue
            if s in ("", "{", "}", "};"):
                continue
            if s.startswith(("package ", "import ", "@VintfStability", "@Backing")):
                continue
            custom_block += line if line.endswith("\n") else line + "\n"
            if prop_re.match(line):
                count += 1
                total_added += 1

    custom_block += "\n"
    print(f"   → {count} properties")

# ── Write merged file ─────────────────────────────────────────────────────────
merged = before_close + custom_block + after_close

with open(TARGET_FILE, "w", encoding="utf-8") as f:
    f.write(merged)

# ── Post-merge verify ─────────────────────────────────────────────────────────
with open(TARGET_FILE, "r", encoding="utf-8") as f:
    verify = f.read()

for required in ["VehicleArea", "VehiclePropertyGroup", "VehiclePropertyType"]:
    if f"import android.hardware.automotive.vehicle.{required}" not in verify:
        print(f"\n❌ POST-MERGE VERIFY FAILED: import {required} missing!")
        sys.exit(1)

print(f"\n✅ {total_added} properties merged into aidl_property successfully")
print(f"   File: {TARGET_FILE}")