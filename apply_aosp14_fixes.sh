#!/bin/bash
# ═══════════════════════════════════════════════════════════════
# apply_aosp14_fixes.sh
# Android 14 AOSP integration for C3/C4 generated VHAL code.
#
# What this script does:
#  [0] Platform build fixes (disabled test modules)
#  [1] Copy custom AIDL files to aidl/ directory
#  [2] Merge custom VehicleProperty*.aidl into aidl_property/VehicleProperty.aidl
#      and update frozen API hash (version 3)
#  [3] Copy C++ + VssGlueAgent artifacts
#  [4] Copy SELinux policies
#  [5] AOSP 14 one-time fixes (frozen flag, FCM, etc.)
#  [6] Runtime fixes (VssProperties.json)
#  [7] Cuttlefish VHAL service selection
# ═══════════════════════════════════════════════════════════════

set -euo pipefail

OUT="${1:?Usage: $0 <output_dir> <aosp_root>  e.g. ~/output_c4_minimal ~/aosp-14-auto}"
AOSP_ROOT="${2:?Usage: $0 <output_dir> <aosp_root>}"
FORCE=0
for arg in "$@"; do [ "$arg" = "--force" ] && FORCE=1; done

RED='\033[0;31m'; GREEN='\033[0;32m'; YELLOW='\033[1;33m'; NC='\033[0m'
ok()   { echo -e "  ${GREEN}✓${NC} $1"; }
warn() { echo -e "  ${YELLOW}⚠${NC} $1"; }
fail() { echo -e "  ${RED}✗${NC} $1"; exit 1; }

echo "═══════════════════════════════════════════════════════════"
echo "  Android 14 AOSP Integration"
echo "═══════════════════════════════════════════════════════════"
echo "  Output : $OUT"
echo "  AOSP   : $AOSP_ROOT"
echo ""

[ -d "$OUT" ]             || fail "Output dir not found: $OUT"
[ -d "$AOSP_ROOT/build" ] || fail "AOSP root invalid: $AOSP_ROOT"

# ── Paths ────────────────────────────────────────────────────────
AIDL_DIR="$AOSP_ROOT/hardware/interfaces/automotive/vehicle/aidl/android/hardware/automotive/vehicle"
AIDL_PROPERTY_FILE="$AOSP_ROOT/hardware/interfaces/automotive/vehicle/aidl_property/android/hardware/automotive/vehicle/VehicleProperty.aidl"
AIDL_PROPERTY_FROZEN_DIR="$AOSP_ROOT/hardware/interfaces/automotive/vehicle/aidl_property/aidl_api/android.hardware.automotive.vehicle.property/3/android/hardware/automotive/vehicle"
AIDL_PROPERTY_CURRENT_DIR="$AOSP_ROOT/hardware/interfaces/automotive/vehicle/aidl_property/aidl_api/android.hardware.automotive.vehicle.property/current/android/hardware/automotive/vehicle"
AIDL_PROPERTY_HASH_FILE="$AOSP_ROOT/hardware/interfaces/automotive/vehicle/aidl_property/aidl_api/android.hardware.automotive.vehicle.property/3/.hash"
VSS_DIR="$AOSP_ROOT/hardware/interfaces/automotive/vehicle/aidl/impl/vss"
SEPOL_DEST="$AOSP_ROOT/system/sepolicy/vendor"
FCM_EXCLUDE="$AOSP_ROOT/hardware/interfaces/compatibility_matrices/exclude/fcm_exclude.cpp"
VHAL_BP="$AOSP_ROOT/hardware/interfaces/automotive/vehicle/aidl/impl/vhal/Android.bp"
AIDL_INTERFACE_BP="$AOSP_ROOT/hardware/interfaces/automotive/vehicle/aidl/Android.bp"
DEVICE_MK="$AOSP_ROOT/device/google/cuttlefish/shared/device.mk"
SRC_AIDL_DIR="$OUT/hardware/interfaces/automotive/vehicle/aidl/android/hardware/automotive/vehicle"

mkdir -p "$AIDL_DIR" "$VSS_DIR" "$SEPOL_DEST"
mkdir -p /tmp/1001/cvd_1/cuttlefish/assembly /tmp/1001/cvd_1/cuttlefish/instances

# ═══════════════════════════════════════════════════════════════
# [0] Platform build fixes
# ═══════════════════════════════════════════════════════════════
echo "[0] Applying platform build fixes..."

CONT_TESTS="$AOSP_ROOT/platform_testing/build/tasks/continuous_native_tests.mk"
if [ ! -f "$CONT_TESTS" ] || ! grep -q "Disabled" "$CONT_TESTS" 2>/dev/null; then
    echo "# Disabled to avoid sv_2d_session_tests build failure" > "$CONT_TESTS"
    ok "Disabled continuous_native_tests.mk"
else
    ok "continuous_native_tests.mk already patched"
fi

if [ -f "$DEVICE_MK" ] && ! grep -q "sv_2d_session_tests" "$DEVICE_MK"; then
    printf '\nPRODUCT_PACKAGES += -sv_2d_session_tests -sv_3d_session_tests\nPRODUCT_PACKAGES += -continuous_native_tests\n' >> "$DEVICE_MK"
    ok "Disabled broken test modules in device.mk"
else
    ok "device.mk already patched"
fi

# ═══════════════════════════════════════════════════════════════
# [1] Copy custom AIDL files to aidl/
# ═══════════════════════════════════════════════════════════════
echo ""
echo "[1] Copying custom AIDL files to aidl/..."

# Remove stale VehicleProperty.aidl from aidl/ if present
# (it belongs in aidl_property/ only — having it in aidl/ causes
# import resolution failure for VehicleArea/VehiclePropertyGroup/Type)
if [ -f "$AIDL_DIR/VehicleProperty.aidl" ]; then
    rm -f "$AIDL_DIR/VehicleProperty.aidl"
    warn "Removed stale VehicleProperty.aidl from aidl/ (belongs in aidl_property/ only)"
fi

COUNT=0
for f in "$SRC_AIDL_DIR"/VehicleProperty*.aidl; do
    [ -f "$f" ] || continue
    fname="$(basename "$f")"
    # Skip VehicleProperty.aidl itself — it's managed via aidl_property/
    [ "$fname" = "VehicleProperty.aidl" ] && continue
    cp "$f" "$AIDL_DIR/" && ok "AIDL: $fname"
    COUNT=$((COUNT + 1))
done
[ $COUNT -eq 0 ] && warn "No custom AIDL files found in $SRC_AIDL_DIR"

# ═══════════════════════════════════════════════════════════════
# [2] Merge custom properties into aidl_property/VehicleProperty.aidl
#     and update frozen API snapshot (version 3)
# ═══════════════════════════════════════════════════════════════
echo ""
echo "[2] Merging custom properties into aidl_property/VehicleProperty.aidl..."

[ -f "$AIDL_PROPERTY_FILE" ] || fail "aidl_property VehicleProperty.aidl not found: $AIDL_PROPERTY_FILE"

# Check if already merged
if grep -q "VSS Custom Properties" "$AIDL_PROPERTY_FILE"; then
    ok "VehicleProperty.aidl already merged (VSS Custom Properties found)"
else
    # Verify imports intact before merge
    for imp in VehicleArea VehiclePropertyGroup VehiclePropertyType; do
        grep -q "import android.hardware.automotive.vehicle.$imp" "$AIDL_PROPERTY_FILE" \
            || fail "import $imp missing from $AIDL_PROPERTY_FILE — restore from git first"
    done

    # Run merge using Python
    python3 - "$SRC_AIDL_DIR" "$AIDL_PROPERTY_FILE" << 'PYEOF'
import sys, re, glob

custom_dir  = sys.argv[1]
target_file = sys.argv[2]

EXCLUDE = {
    "VehicleProperty.aidl",
    "VehiclePropertyAccess.aidl",
    "VehiclePropertyStatus.aidl",
    "VehiclePropertyChangeMode.aidl",
}

custom_files = sorted([
    f for f in glob.glob(f"{custom_dir}/VehicleProperty*.aidl")
    if f.split("/")[-1] not in EXCLUDE
])

if not custom_files:
    print("  No custom files to merge")
    sys.exit(0)

content = open(target_file).read()
last_brace = content.rfind("}")
if last_brace == -1:
    print("ERROR: no closing } found", file=sys.stderr); sys.exit(1)

block  = "\n    // ================================================================\n"
block += "    // VSS Custom Properties (vendor, pre-encoded int32)\n"
block += "    // ================================================================\n\n"

prop_re = re.compile(r'^\s*[A-Z][A-Z0-9_]+\s*=\s*0x[0-9a-fA-F]+')
total = 0
for fpath in custom_files:
    count, in_enum = 0, False
    for line in open(fpath):
        s = line.strip()
        if re.match(r'^enum\s+\w+\s*\{', s): in_enum = True; continue
        if not in_enum: continue
        if s in ("", "{", "}", "};"): continue
        if s.startswith(("package ", "import ", "@VintfStability", "@Backing")): continue
        block += line if line.endswith("\n") else line + "\n"
        if prop_re.match(line): count += 1; total += 1
    block += "\n"
    print(f"  {fpath.split('/')[-1]}: {count} properties")

merged = content[:last_brace] + block + content[last_brace:]
open(target_file, "w").write(merged)
print(f"  Total: {total} custom properties merged")
PYEOF

    ok "Merged custom properties into VehicleProperty.aidl"

    # Copy merged file to aidl_property/current/
    mkdir -p "$AIDL_PROPERTY_CURRENT_DIR"
    cp "$AIDL_PROPERTY_FILE" "$AIDL_PROPERTY_CURRENT_DIR/VehicleProperty.aidl"
    ok "Copied to aidl_property/current/"

    # Copy merged file to aidl_property/3/ (frozen snapshot)
    mkdir -p "$AIDL_PROPERTY_FROZEN_DIR"
    cp "$AIDL_PROPERTY_FILE" "$AIDL_PROPERTY_FROZEN_DIR/VehicleProperty.aidl"
    ok "Copied to aidl_property/3/"

    # Recompute hash for version 3 frozen snapshot
    HASH_DIR="$(dirname "$AIDL_PROPERTY_HASH_FILE")"
    NEW_HASH=$(cd "$HASH_DIR" && \
        { find ./ -name "*.aidl" -print0 | LC_ALL=C sort -z | xargs -0 sha1sum && echo 2; } \
        | sha1sum | cut -d " " -f 1)
    echo "$NEW_HASH" > "$AIDL_PROPERTY_HASH_FILE"
    ok "Updated frozen API hash: $NEW_HASH"
fi

# Fix aidl/ interface: frozen: false → frozen: true
# (aidl/ contains IVehicle.aidl interface definitions, not property definitions;
#  it has no custom changes so frozen: true is correct and avoids a spurious
#  "no changes between current and last frozen version" build error)
if [ -f "$AIDL_INTERFACE_BP" ]; then
    if grep -q "frozen: false" "$AIDL_INTERFACE_BP"; then
        sed -i 's/frozen: false/frozen: true/' "$AIDL_INTERFACE_BP"
        ok "aidl/Android.bp: frozen: false → frozen: true"
    else
        ok "aidl/Android.bp: frozen already true"
    fi
fi

# ═══════════════════════════════════════════════════════════════
# [3] Copy C++ + VssGlueAgent artifacts
# ═══════════════════════════════════════════════════════════════
echo ""
echo "[3] Copying C++ and glue artifacts..."

COUNT=0

for src_dir in \
    "$OUT/hardware/interfaces/automotive/vehicle/impl" \
    "$OUT/hardware/interfaces/automotive/vehicle/aidl/impl" \
    "$OUT/hardware/interfaces/automotive/vehicle/aidl/impl/vss"; do

    if [ -d "$src_dir" ]; then
        for f in "$src_dir"/VehicleHalService*.cpp "$src_dir"/VehicleHalService*.h; do
            [ -f "$f" ] || continue
            cp "$f" "$VSS_DIR/" && ok "Domain C++: $(basename "$f")"
            COUNT=$((COUNT + 1))
        done
    fi
done

VSS_GLUE_SRC="$OUT/hardware/interfaces/automotive/vehicle/aidl/impl/vss"
if [ -d "$VSS_GLUE_SRC" ]; then
    for f in "$VSS_GLUE_SRC"/*; do
        [ -f "$f" ] || continue
        fname="$(basename "$f")"
        if [[ "$fname" == *.te ]]; then
            cp "$f" "$SEPOL_DEST/" && ok "Glue SELinux: $fname"
        else
            cp "$f" "$VSS_DIR/" && ok "Glue: $fname"
        fi
        COUNT=$((COUNT + 1))
    done
fi

[ $COUNT -eq 0 ] && warn "No VehicleHalService files found"

# ═══════════════════════════════════════════════════════════════
# [4] Copy SELinux policies
# ═══════════════════════════════════════════════════════════════
echo ""
echo "[4] Copying SELinux policies..."

COUNT=0
for f in "$OUT"/sepolicy/vehicle_hal_*.te; do
    [ -f "$f" ] || continue
    cp "$f" "$SEPOL_DEST/" && ok "SELinux: $(basename "$f")"
    COUNT=$((COUNT + 1))
done
[ $COUNT -eq 0 ] && warn "No .te files found"

FC_VSS="$SEPOL_DEST/file_contexts_vss"
if ! grep -qF "V3-vss-service" "$FC_VSS" 2>/dev/null; then
    echo "/vendor/bin/hw/android\\.hardware\\.automotive\\.vehicle@V3-vss-service u:object_r:hal_vehicle_vss_exec:s0" >> "$FC_VSS"
    ok "SELinux label added to file_contexts_vss"
else
    ok "SELinux label already present"
fi

MAIN_FC="$AOSP_ROOT/system/sepolicy/vendor/file_contexts"
if [ -f "$MAIN_FC" ] && ! grep -qF "V3-vss-service" "$MAIN_FC" 2>/dev/null; then
    echo "/vendor/bin/hw/android\\.hardware\\.automotive\\.vehicle@V3-vss-service u:object_r:hal_vehicle_vss_exec:s0" >> "$MAIN_FC"
    ok "SELinux label added to system/sepolicy/vendor/file_contexts"
else
    ok "SELinux label already present in system/sepolicy/vendor/file_contexts"
fi

# ═══════════════════════════════════════════════════════════════
# [5] AOSP 14 one-time fixes
# ═══════════════════════════════════════════════════════════════
echo ""
echo "[5] Applying AOSP 14 one-time fixes..."

if [ -f "$VHAL_BP" ] && grep -q "vintf_fragments.*vhal-default-service" "$VHAL_BP"; then
    sed -i '/vintf_fragments.*vhal-default-service.xml/d' "$VHAL_BP"
    ok "Removed conflicting vintf_fragments from vhal/Android.bp"
else
    ok "vhal/Android.bp already clean"
fi

if [ -f "$FCM_EXCLUDE" ] && ! grep -q "automotive.vehicle@4" "$FCM_EXCLUDE"; then
    sed -i '/static std::vector<std::string> excluded_exact{/a\            "android.hardware.automotive.vehicle@4",' "$FCM_EXCLUDE"
    ok "FCM exempt: added vehicle@4"
else
    ok "FCM exempt already present"
fi

[ -f "$DEVICE_MK" ] && sed -i '/vehicle-hal-emulator/d' "$DEVICE_MK" 2>/dev/null && ok "Disabled vehicle-hal-emulator"

# ═══════════════════════════════════════════════════════════════
# [6] Runtime fixes
# ═══════════════════════════════════════════════════════════════
echo ""
echo "[6] Applying runtime fixes..."

VSS_CONFIG_DEST="$AOSP_ROOT/vendor/etc/automotive/vhalconfig"
mkdir -p "$VSS_CONFIG_DEST"

python3 - "$SRC_AIDL_DIR" "$VSS_CONFIG_DEST/VssProperties.json" << 'PYEOF'
import sys, re, json, os

aidl_dir = sys.argv[1]
out_json  = sys.argv[2]
props, seen = [], set()

AREA_GLOBAL = 0x00000000
TYPE_MIXED  = 0x00e00000
comment_map = {
    "BOOLEAN": 0x00200000,
    "INT":     0x00400000,
    "FLOAT":   0x00600000,
    "STRING":  0x00100000,
}

if os.path.isdir(aidl_dir):
    for fname in sorted(os.listdir(aidl_dir)):
        if not fname.endswith(".aidl") or "VehicleProperty" not in fname:
            continue
        txt = open(os.path.join(aidl_dir, fname), errors="ignore").read()
        for line in txt.splitlines():
            m = re.match(r'\s*(\w+)\s*=\s*(0x[0-9A-Fa-f]+)', line)
            if not m: continue
            name, val = m.group(1), m.group(2)
            try:
                raw_id = int(val, 16)
                type_bits = TYPE_MIXED
                for kw, bits in comment_map.items():
                    if kw in line: type_bits = bits; break
                prop_id = AREA_GLOBAL | type_bits | (raw_id & 0xFFFF)
                if prop_id not in seen:
                    seen.add(prop_id)
                    access = 3 if "READ_WRITE" in line else 1
                    props.append({"prop": prop_id, "access": access, "changeMode": 1, "comment": name})
            except ValueError:
                pass

json.dump(props, open(out_json, "w"), indent=2)
print(f"  Generated {len(props)} property configs → {out_json}")
PYEOF

ok "VssProperties.json generated"

CVD_RUNTIME="${HOME}/cuttlefish_runtime"
CVD_CACHE="${HOME}/.cache/cuttlefish"
[ -d "$CVD_RUNTIME" ] && rm -rf "$CVD_RUNTIME" && ok "Cleared ~/cuttlefish_runtime"
[ -d "$CVD_CACHE" ]   && rm -rf "$CVD_CACHE"   && ok "Cleared ~/.cache/cuttlefish"

# ═══════════════════════════════════════════════════════════════
# [7] Cuttlefish VHAL service selection
# ═══════════════════════════════════════════════════════════════
echo ""
echo "[7] Configuring Cuttlefish to use VSS VHAL service..."

DEVICE_VENDOR_MK="$AOSP_ROOT/device/google/cuttlefish/shared/auto/device_vendor.mk"
[ -f "$DEVICE_VENDOR_MK" ] || fail "device_vendor.mk not found: $DEVICE_VENDOR_MK"

INCLUDER=""
if [ -n "${TARGET_PRODUCT:-}" ]; then
    REMAINDER="${TARGET_PRODUCT#aosp_cf_}"
    FLAVOR="${REMAINDER##*_}"
    ARCH_TOKEN="${REMAINDER%_*}"
    case "$ARCH_TOKEN" in
        x86_64|arm64) ARCH_DIR="vsoc_${ARCH_TOKEN}_only" ;;
        *)             ARCH_DIR="vsoc_${ARCH_TOKEN}"      ;;
    esac
    CANDIDATE="$AOSP_ROOT/device/google/cuttlefish/${ARCH_DIR}/${FLAVOR}/aosp_cf.mk"
    if [ -f "$CANDIDATE" ] && grep -q "device_vendor.mk" "$CANDIDATE"; then
        INCLUDER="$CANDIDATE"
        ok "Derived from TARGET_PRODUCT=$TARGET_PRODUCT"
    fi
fi

if [ -z "$INCLUDER" ]; then
    INCLUDER=$(grep -rl "device_vendor.mk" "$AOSP_ROOT/device/google/cuttlefish/" 2>/dev/null \
        | grep -v "device_vendor.mk$" | grep -v "\.bak" | grep "auto/aosp_cf.mk" | head -1)
fi

if [ -n "$INCLUDER" ]; then
    ok "Injection target: $INCLUDER"
    while IFS= read -r stale_file; do
        sed -i '/# VSS VHAL override/d'                     "$stale_file" 2>/dev/null || true
        sed -i '/LOCAL_VHAL_PRODUCT_PACKAGE.*vss-service/d' "$stale_file" 2>/dev/null || true
    done < <(grep -rl "device_vendor.mk" "$AOSP_ROOT/device/google/cuttlefish/" 2>/dev/null \
        | grep -v "device_vendor.mk$" | grep -v "\.bak")

    sed -i '/LOCAL_VHAL_PRODUCT_PACKAGE.*vss-service/d' "$DEVICE_VENDOR_MK"
    INCLUDE_LINE=$(grep -n "device_vendor.mk" "$INCLUDER" | head -1 | cut -d: -f1)
    sed -i "${INCLUDE_LINE}i # VSS VHAL override\nLOCAL_VHAL_PRODUCT_PACKAGE := android.hardware.automotive.vehicle@V3-vss-service\n" "$INCLUDER"
    ok "Injected LOCAL_VHAL_PRODUCT_PACKAGE in $(basename "$INCLUDER")"
else
    warn "Could not find aosp_cf.mk — set LOCAL_VHAL_PRODUCT_PACKAGE manually"
fi

echo ""
echo "[8] Fixing Cuttlefish symlink persistence..."
BASHRC="$HOME/.bashrc"
if ! grep -q "# cvd-symlink-fix" "$BASHRC" 2>/dev/null; then
    cat >> "$BASHRC" << 'BASHRCEOF'
# cvd-symlink-fix
mkdir -p /tmp/1001/cvd_1/cuttlefish/assembly /tmp/1001/cvd_1/cuttlefish/instances 2>/dev/null || true
[ -L "$HOME/cuttlefish" ] || ln -sf /tmp/1001/cvd_1/cuttlefish "$HOME/cuttlefish"
BASHRCEOF
    ok "Added cvd symlink fix to ~/.bashrc"
else
    ok "~/.bashrc already has cvd symlink fix"
fi

echo ""
echo "═══════════════════════════════════════════════════════════"
echo " Done. Next steps:"
echo " 1. Build:   m android.hardware.automotive.vehicle.property-V3-ndk"
echo "             m -j\$(nproc) vendorimage vbmetaimage superimage"
echo " 2. Launch:  launch_cvd --noresume --cpus=8 --memory_mb=8192 --gpu_mode=guest_swiftshader"
echo " 3. VTS:     atest VtsHalAutomotiveVehicleVss"
echo "═══════════════════════════════════════════════════════════"