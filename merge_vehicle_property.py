#!/usr/bin/env python3
"""
Merge VehicleProperty*.aidl -> VehicleProperty.aidl
Compatible with Android 14

Usage:
    python3 merge_vehicle_property.py <aidl_directory>
"""

import os
import glob
import sys

if len(sys.argv) != 2:
    print("Usage: python3 merge_vehicle_property.py <aidl_directory>")
    sys.exit(1)

AIDL_DIR = sys.argv[1]

if not os.path.isdir(AIDL_DIR):
    print(f"Directory not found: {AIDL_DIR}")
    sys.exit(1)

# ---------------------------------------------------------------------
# Collect input files
# ---------------------------------------------------------------------

files = sorted(glob.glob(os.path.join(AIDL_DIR, "VehicleProperty*.aidl")))

# Skip output file if already exists
files = [
    f for f in files
    if os.path.basename(f) != "VehicleProperty.aidl"
]

if not files:
    print("No VehicleProperty*.aidl files found.")
    sys.exit(1)

output = os.path.join(AIDL_DIR, "VehicleProperty.aidl")

print(f"Merging {len(files)} files...")
print()

# ---------------------------------------------------------------------
# Write output
# ---------------------------------------------------------------------

with open(output, "w", encoding="utf-8") as out:

    out.write("""package android.hardware.automotive.vehicle;

@VintfStability
@Backing(type="int")
enum VehicleProperty {

""")

    total = 0

    for filepath in files:

        filename = os.path.basename(filepath)
        group = filename.replace("VehicleProperty", "").replace(".aidl", "")

        print(f"  - {filename}")

        out.write(f"    // ==================================================\n")
        out.write(f"    // {group}\n")
        out.write(f"    // ==================================================\n")

        inside_enum = False
        count = 0

        with open(filepath, "r", encoding="utf-8") as src:

            for line in src:

                stripped = line.strip()

                # Start enum
                if stripped.startswith("enum "):
                    inside_enum = True
                    continue

                if not inside_enum:
                    continue

                # Skip braces
                if stripped in ("{", "}", "};"):
                    continue

                # Skip blank lines
                if stripped == "":
                    continue

                out.write(line)

                # Count property lines only
                if "=" in stripped and stripped.endswith(","):
                    count += 1
                    total += 1

        out.write("\n")

    out.write("}\n")

print()
print("======================================")
print(f"Output : {output}")
print(f"Files  : {len(files)}")
print(f"Entries: {total}")
print("Done.")