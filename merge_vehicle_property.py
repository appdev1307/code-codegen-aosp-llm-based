#!/usr/bin/env python3
"""
Merge custom VehicleProperty*.aidl - Final Version
Giữ header + import, không xóa file
"""

import os
import glob
import re
import sys
import shutil

if len(sys.argv) != 2:
    print("Usage: python3 merge_vehicle_property_safe.py <aidl_directory>")
    sys.exit(1)

AIDL_DIR = sys.argv[1]

if not os.path.isdir(AIDL_DIR):
    print("❌ Directory not found:", AIDL_DIR)
    sys.exit(1)

EXCLUDE = {
    "VehicleProperty.aidl",
    "VehiclePropertyAccess.aidl",
    "VehiclePropertyStatus.aidl",
    "VehiclePropertyChangeMode.aidl",
}

custom_files = [
    f for f in sorted(glob.glob(os.path.join(AIDL_DIR, "VehicleProperty*.aidl")))
    if os.path.basename(f) not in EXCLUDE
]

if not custom_files:
    print("Không tìm thấy file custom.")
    sys.exit(0)

main_file = os.path.join(AIDL_DIR, "VehicleProperty.aidl")

print(f"🔄 Đang merge {len(custom_files)} file custom vào {main_file}\n")

# Copy file gốc nếu chưa có
if not os.path.exists(main_file):
    source_main = "/home/nguyenngoctam1307/aosp-14-auto/hardware/interfaces/automotive/vehicle/aidl/android/hardware/automotive/vehicle/VehicleProperty.aidl"
    if os.path.exists(source_main):
        shutil.copy2(source_main, main_file)
        print("📋 Đã copy file gốc từ source.")
    else:
        print("❌ Không tìm thấy file gốc!")
        sys.exit(1)

# Đọc file gốc
with open(main_file, "r", encoding="utf-8") as f:
    content = f.read()

# Đảm bảo có đầy đủ import
if "VehicleArea" not in content or "VehiclePropertyGroup" not in content:
    header = """package android.hardware.automotive.vehicle;

import android.hardware.automotive.vehicle.VehicleArea;
import android.hardware.automotive.vehicle.VehiclePropertyGroup;
import android.hardware.automotive.vehicle.VehiclePropertyType;
"""
    # Giữ comment license nếu có
    if content.startswith("/*"):
        license_end = content.find("*/") + 2
        content = content[:license_end] + "\n\n" + header + content[license_end:]
    else:
        content = header + content

# Thêm custom properties
content = content.rstrip("}\n ") + "\n\n"
content += "    // ==================================================\n"
content += "    // Vendor / Custom Properties\n"
content += "    // ==================================================\n\n"

total_added = 0
property_pattern = re.compile(r'^\s*[A-Za-z0-9_]+\s*=')

for filepath in custom_files:
    filename = os.path.basename(filepath)
    print(f"📄 Đang merge: {filename}")
    count = 0
    inside = False
    with open(filepath, encoding="utf-8") as src:
        for line in src:
            s = line.strip()
            if s.startswith("enum "):
                inside = True
                continue
            if not inside or s in ("{", "}", "};", ""):
                continue
            content += line
            if property_pattern.match(line):
                count += 1
                total_added += 1
    content += "\n"
    print(f"   → Đã thêm {count} properties")

content += "}\n"

with open(main_file, "w", encoding="utf-8") as f:
    f.write(content)

# Copy sang aidl_property
aidl_property_dir = AIDL_DIR.replace("/aidl/", "/aidl_property/")
aidl_property_file = os.path.join(aidl_property_dir, "VehicleProperty.aidl")

if os.path.exists(aidl_property_dir):
    shutil.copy2(main_file, aidl_property_file)
    print(f"📋 Đã copy sang aidl_property: {aidl_property_file}")

print(f"\n✅ Merge hoàn tất! ({total_added} properties)")
print("Done.")