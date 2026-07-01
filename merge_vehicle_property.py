#!/usr/bin/env python3
"""
Merge custom VehicleProperty*.aidl an toàn + copy sang aidl_property
**KHÔNG xóa** file custom
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
    print("ℹ️ Không tìm thấy file custom.")
    sys.exit(0)

main_file = os.path.join(AIDL_DIR, "VehicleProperty.aidl")

print(f"🔄 Đang merge {len(custom_files)} file custom vào {main_file}\n")

with open(main_file, "r", encoding="utf-8") as f:
    content = f.read()

if not content.strip().endswith("}"):
    print("❌ File VehicleProperty.aidl không hợp lệ.")
    sys.exit(1)

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
            if not inside:
                continue
            if s in ("{", "}", "};", ""):
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

print(f"\n✅ Merge hoàn tất! Tổng {total_added} properties custom.")

# Copy sang aidl_property
aidl_property_dir = AIDL_DIR.replace("/aidl/", "/aidl_property/")
aidl_property_file = os.path.join(aidl_property_dir, "VehicleProperty.aidl")

if os.path.exists(aidl_property_dir):
    shutil.copy2(main_file, aidl_property_file)
    print(f"📋 Đã copy sang aidl_property: {aidl_property_file}")
else:
    print("⚠️ Không tìm thấy thư mục aidl_property")

print("Done. (Không xóa bất kỳ file custom nào)")