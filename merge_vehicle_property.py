#!/usr/bin/env python3
"""
Merge custom VehicleProperty*.aidl into VehicleProperty.aidl
Android 14

Usage:
    python3 merge_vehicle_property.py <aidl_directory>
"""

import os
import glob
import re
import sys

if len(sys.argv) != 2:
    print("Usage:")
    print("python3 merge_vehicle_property.py <aidl_directory>")
    sys.exit(1)

AIDL_DIR = sys.argv[1]

if not os.path.isdir(AIDL_DIR):
    print("Directory not found:", AIDL_DIR)
    sys.exit(1)

EXCLUDE = {
    "VehicleProperty.aidl",
    "VehiclePropertyAccess.aidl",
    "VehiclePropertyStatus.aidl",
    "VehiclePropertyChangeMode.aidl",
}

files = [
    f for f in sorted(glob.glob(os.path.join(AIDL_DIR, "VehicleProperty*.aidl")))
    if os.path.basename(f) not in EXCLUDE
]

if not files:
    print("No custom VehicleProperty*.aidl found.")
    sys.exit(1)

output = os.path.join(AIDL_DIR, "VehicleProperty.aidl")

# Remove old merged file
if os.path.exists(output):
    os.remove(output)

property_pattern = re.compile(r'^\s*[A-Za-z0-9_]+\s*=')

total = 0

print(f"Merging {len(files)} files...\n")

with open(output, "w", encoding="utf-8") as out:

    out.write("""package android.hardware.automotive.vehicle;

@VintfStability
@Backing(type="int")
enum VehicleProperty {

""")

    for filepath in files:

        filename = os.path.basename(filepath)
        group = filename.replace("VehicleProperty", "").replace(".aidl", "")

        print(" -", filename)

        out.write(f"    // ==================================================\n")
        out.write(f"    // {group}\n")
        out.write(f"    // ==================================================\n")

        inside = False
        count = 0

        with open(filepath, encoding="utf-8") as src:

            for line in src:

                s = line.strip()

                if s.startswith("enum "):
                    inside = True
                    continue

                if not inside:
                    continue

                if s in ("{", "}", "};", ""):
                    continue

                out.write(line)

                if property_pattern.match(line):
                    count += 1
                    total += 1

        out.write("\n")

        print(f"   -> {count} properties")

out.write

with open(output, "a", encoding="utf-8") as out:
    out.write("}\n")

print("\nRemoving intermediate files...")

for f in files:
    print(" -", os.path.basename(f))
    os.remove(f)

print("\n====================================")
print("Output :", output)
print("Files  :", len(files))
print("Props  :", total)
print("Done.")