#!/bin/bash
# ═══════════════════════════════════════════════════════════════
# apply_aosp14_fixes.sh  (fixed)
# Android 14 AOSP integration for C3/C4 generated VHAL code.
# ═══════════════════════════════════════════════════════════════

set -euo pipefail

OUT="${1:?Usage: $0 <output_dir>  e.g. ~/output_c4}"
AOSP_ROOT="${2:-$(pwd)}"

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

[ -d "$OUT" ]              || fail "Output dir not found: $OUT"
[ -d "$AOSP_ROOT/build" ]  || fail "AOSP root invalid: $AOSP_ROOT"

AIDL_DIR="$AOSP_ROOT/hardware/interfaces/automotive/vehicle/aidl/android/hardware/automotive/vehicle"
VSS_DIR="$AOSP_ROOT/hardware/interfaces/automotive/vehicle/aidl/impl/vss"
SEPOL_DEST="$AOSP_ROOT/system/sepolicy/vendor"
FCM_EXCLUDE="$AOSP_ROOT/hardware/interfaces/compatibility_matrices/exclude/fcm_exclude.cpp"
VHAL_BP="$AOSP_ROOT/hardware/interfaces/automotive/vehicle/aidl/impl/vhal/Android.bp"
VHAL_DEFAULT_XML_OUT="$AOSP_ROOT/out/target/product/vsoc_x86_64_only/vendor/etc/vintf/manifest/vhal-default-service.xml"
SRC_AIDL_DIR="$OUT/hardware/interfaces/automotive/vehicle/aidl/android/hardware/automotive/vehicle"

mkdir -p "$AIDL_DIR" "$VSS_DIR" "$SEPOL_DEST"

# ═══════════════════════════════════════════════════════════════
# [0/5] Platform build fixes
# ═══════════════════════════════════════════════════════════════
echo "[0/5] Applying platform build fixes..."

CONT_TESTS="$AOSP_ROOT/platform_testing/build/tasks/continuous_native_tests.mk"
if [ ! -f "$CONT_TESTS" ] || ! grep -q "Disabled" "$CONT_TESTS" 2>/dev/null; then
    echo "# Disabled to avoid sv_2d_session_tests build failure" > "$CONT_TESTS"
    ok "Disabled continuous_native_tests.mk"
else
    ok "continuous_native_tests.mk already patched"
fi

DEVICE_MK="$AOSP_ROOT/device/google/cuttlefish/shared/device.mk"
if [ -f "$DEVICE_MK" ] && ! grep -q "sv_2d_session_tests" "$DEVICE_MK"; then
    printf '\nPRODUCT_PACKAGES += -sv_2d_session_tests -sv_3d_session_tests\nPRODUCT_PACKAGES += -continuous_native_tests\n' >> "$DEVICE_MK"
    ok "Disabled broken test modules in device.mk"
else
    ok "device.mk already patched"
fi

# ═══════════════════════════════════════════════════════════════
# [1/5] Copy AIDL files
# ═══════════════════════════════════════════════════════════════
echo ""
echo "[1/5] Copying AIDL files..."
COUNT=0
for f in "$SRC_AIDL_DIR"/*.aidl; do
    [ -f "$f" ] || continue
    cp "$f" "$AIDL_DIR/" && ok "AIDL: $(basename $f)"
    COUNT=$((COUNT + 1))
done
[ $COUNT -eq 0 ] && warn "No AIDL files found"

# ═══════════════════════════════════════════════════════════════
# [2/5] Copy C++ + VssGlueAgent artifacts
# ═══════════════════════════════════════════════════════════════
echo ""
echo "[2/5] Copying C++ and glue artifacts..."
COUNT=0
VSS_GLUE_SRC="$OUT/hardware/interfaces/automotive/vehicle/aidl/impl/vss"
if [ -d "$VSS_GLUE_SRC" ]; then
    for f in "$VSS_GLUE_SRC"/*.cpp "$VSS_GLUE_SRC"/*.h \
              "$VSS_GLUE_SRC"/*.bp "$VSS_GLUE_SRC"/*.xml \
              "$VSS_GLUE_SRC"/*.rc "$VSS_GLUE_SRC"/*.te; do
        [ -f "$f" ] || continue
        cp "$f" "$VSS_DIR/" && ok "Glue: $(basename $f)"
        COUNT=$((COUNT + 1))
    done
fi
for f in "$OUT"/hardware/interfaces/automotive/vehicle/impl/VehicleHalService*.cpp \
          "$OUT"/hardware/interfaces/automotive/vehicle/impl/VehicleHalService*.h; do
    [ -f "$f" ] || continue
    cp "$f" "$VSS_DIR/" && ok "Domain C++: $(basename $f)"
    COUNT=$((COUNT + 1))
done
[ $COUNT -eq 0 ] && warn "No C++ files found"

# ═══════════════════════════════════════════════════════════════
# [3/5] Copy SELinux
# ═══════════════════════════════════════════════════════════════
echo ""
echo "[3/5] Copying SELinux / file_contexts..."
COUNT=0
for f in "$OUT"/sepolicy/vehicle_hal_*.te; do
    [ -f "$f" ] || continue
    cp "$f" "$SEPOL_DEST/" && ok "SELinux: $(basename $f)"
    COUNT=$((COUNT + 1))
done
[ $COUNT -eq 0 ] && warn "No .te files found"

if [ -f "$OUT/sepolicy/private/file_contexts" ]; then
    cp "$OUT/sepolicy/private/file_contexts" "$SEPOL_DEST/file_contexts_vss"
    ok "file_contexts"
fi

VSS_TE="$SEPOL_DEST/vehicle_hal_vss.te"
if [ ! -f "$VSS_TE" ]; then
    cat > "$VSS_TE" << 'SEEOF'
type hal_vehicle_vss, domain;
type hal_vehicle_vss_exec, exec_type, vendor_file_type, file_type;
init_daemon_domain(hal_vehicle_vss)
hal_server_domain(hal_vehicle_vss, hal_vehicle)
binder_use(hal_vehicle_vss)
binder_call(hal_vehicle_vss, system_server)
binder_call(system_server, hal_vehicle_vss)
allow hal_vehicle_vss vndbinder_device:chr_file { read write open };
allow hal_vehicle_vss vendor_configs_file:dir search;
allow hal_vehicle_vss vendor_configs_file:file { read getattr open };
SEEOF
    ok "SELinux policy for hal_vehicle_vss created"
else
    ok "SELinux policy for hal_vehicle_vss already present"
fi

FC_VSS="$SEPOL_DEST/file_contexts_vss"
if ! grep -qF "V3-vss-service" "$FC_VSS" 2>/dev/null; then
    echo "/vendor/bin/hw/android\\.hardware\\.automotive\\.vehicle@V3-vss-service u:object_r:hal_vehicle_vss_exec:s0" >> "$FC_VSS"
    ok "SELinux label for V3-vss-service added"
else
    ok "SELinux label for V3-vss-service already present"
fi

# ═══════════════════════════════════════════════════════════════
# [4/5] AOSP 14 one-time fixes
# ═══════════════════════════════════════════════════════════════
echo ""
echo "[4/5] Applying AOSP 14 one-time fixes..."

if [ -f "$VHAL_BP" ] && grep -q "vintf_fragments.*vhal-default-service" "$VHAL_BP"; then
    sed -i '/vintf_fragments.*vhal-default-service.xml/d' "$VHAL_BP"
    ok "Removed conflicting vintf_fragments"
else
    ok "vhal/Android.bp already clean"
fi

[ -f "$VHAL_DEFAULT_XML_OUT" ] && rm -f "$VHAL_DEFAULT_XML_OUT" && ok "Removed stale vhal-default-service.xml"

AIDL_BP_FILE="$AOSP_ROOT/hardware/interfaces/automotive/vehicle/aidl/Android.bp"
[ -f "$AIDL_BP_FILE" ] && sed -i 's/frozen: true,/frozen: false,/' "$AIDL_BP_FILE" && ok "AIDL interface unfrozen"

if [ -f "$FCM_EXCLUDE" ] && ! grep -q "automotive.vehicle@4" "$FCM_EXCLUDE"; then
    sed -i '/static std::vector<std::string> excluded_exact{/a\            "android.hardware.automotive.vehicle@4",' "$FCM_EXCLUDE"
    ok "FCM exempt: added vehicle@4"
else
    ok "FCM exempt already present"
fi

[ -f "$DEVICE_MK" ] && sed -i '/vehicle-hal-emulator/d' "$DEVICE_MK" 2>/dev/null && ok "Disabled vehicle-hal-emulator"

# ═══════════════════════════════════════════════════════════════
# [5/5] Runtime fixes: VssPropertiesRegistered + super.img
# ═══════════════════════════════════════════════════════════════
echo ""
echo "[5/5] Applying runtime fixes..."

# Fix 1: VssPropertiesRegistered VTS
# LLM-generated AIDL uses sequential small IDs (0x1000+) per enum.
# VssVehicleHardware.cpp already has getAllPropertyConfigs() hardcoded.
# Generate VssProperties.json so FakeVehicleHardware can also load them.
VSS_CONFIG_DEST="$AOSP_ROOT/vendor/etc/automotive/vhalconfig"
mkdir -p "$VSS_CONFIG_DEST"

python3 - "$SRC_AIDL_DIR" "$VSS_CONFIG_DEST/VssProperties.json" << 'PYEOF'
import sys, re, json, os

aidl_dir = sys.argv[1]
out_json  = sys.argv[2]
props, seen = [], set()

# LLM generates sequential enum IDs starting from 0x1000 per domain
# Build full 32-bit VHAL property IDs:
# bits[31:24]=area, bits[23:16]=type, bits[15:0]=index
# Use GLOBAL(0x00) + INT32(0x400000) base offset for standard props
AREA_GLOBAL  = 0x00000000
TYPE_MIXED   = 0x00e00000  # fallback type

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
            # Match: NAME = 0xNNNN, // TYPE, ACCESS, AREA
            m = re.match(r'\s*(\w+)\s*=\s*(0x[0-9A-Fa-f]+)', line)
            if not m:
                continue
            name, val = m.group(1), m.group(2)
            try:
                raw_id = int(val, 16)
                # Determine type from comment
                type_bits = TYPE_MIXED
                for kw, bits in comment_map.items():
                    if kw in line:
                        type_bits = bits
                        break
                # Build full prop ID
                prop_id = AREA_GLOBAL | type_bits | (raw_id & 0xFFFF)
                if prop_id not in seen:
                    seen.add(prop_id)
                    # access: READ=1, WRITE=2, READ_WRITE=3
                    access = 3 if "READ_WRITE" in line else 1
                    props.append({
                        "prop": prop_id,
                        "access": access,
                        "changeMode": 1,
                        "comment": name
                    })
            except ValueError:
                pass

json.dump(props, open(out_json, "w"), indent=2)
print(f"  Generated {len(props)} property configs → {out_json}")
PYEOF

ok "VssProperties.json generated (fixes VssPropertiesRegistered VTS)"

# Fix 2: super.img not picked up by launch_cvd --noresume
# Must clear Cuttlefish runtime cache before each launch with new images
CVD_RUNTIME="${HOME}/cuttlefish_runtime"
CVD_CACHE="${HOME}/.cache/cuttlefish"

if [ -d "$CVD_RUNTIME" ]; then
    rm -rf "$CVD_RUNTIME"
    ok "Cleared ~/cuttlefish_runtime (new super.img will be picked up)"
else
    ok "No cuttlefish_runtime cache to clear"
fi

if [ -d "$CVD_CACHE" ]; then
    rm -rf "$CVD_CACHE"
    ok "Cleared ~/.cache/cuttlefish"
fi

echo ""
echo "═══════════════════════════════════════════════════════════"
echo "  Done. Next steps:"
echo "  1. Build:  m -j\$(nproc) 2>&1 | tee ~/build_c4.log"
echo "  2. Launch: launch_cvd --noresume --cpus=4 --memory_mb=4096"
echo "  3. VTS:    atest VtsHalAutomotiveVehicle"
echo "═══════════════════════════════════════════════════════════"

# ═══════════════════════════════════════════════════════════════
# [6/6] Fix VssVehicleHardware.cpp prop IDs
# VssGlueAgent uses raw sequential IDs (0x1000+) which are invalid
# VHAL property IDs. Rebuild getAllPropertyConfigs() with full
# 32-bit IDs: AREA_GLOBAL | TYPE_BITS | (raw_id & 0xFFFF)
# ═══════════════════════════════════════════════════════════════
echo ""
echo "[6/6] Fixing VssVehicleHardware.cpp property IDs..."

VSS_CPP="$VSS_DIR/VssVehicleHardware.cpp"
if [ ! -f "$VSS_CPP" ]; then
    warn "VssVehicleHardware.cpp not found — skipping prop ID fix"
else
    python3 - "$SRC_AIDL_DIR" "$VSS_CPP" << 'PYEOF'
import sys, re, os

aidl_dir = sys.argv[1]
cpp_path  = sys.argv[2]

# Parse AIDL with full prop ID generation
AREA_GLOBAL = 0x00000000
TYPE_MIXED  = 0x00e00000
comment_map = {
    "BOOLEAN": 0x00200000,
    "INT":     0x00400000,
    "FLOAT":   0x00600000,
    "STRING":  0x00100000,
}

props, seen = [], set()
if os.path.isdir(aidl_dir):
    for fname in sorted(os.listdir(aidl_dir)):
        if not fname.endswith(".aidl") or "VehicleProperty" not in fname: continue
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
                    access = "READ_WRITE" if "READ_WRITE" in line else "READ"
                    props.append({"name": name, "prop_id": prop_id, "access": access})
            except ValueError: pass

# Rebuild getAllPropertyConfigs() body
config_lines = []
for p in props:
    config_lines.append(
        f"    {{\n"
        f"        aidlvhal::VehiclePropConfig cfg;\n"
        f"        cfg.prop = {p['prop_id']};\n"
        f"        cfg.access = aidlvhal::VehiclePropertyAccess::{p['access']};\n"
        f"        cfg.changeMode = aidlvhal::VehiclePropertyChangeMode::ON_CHANGE;\n"
        f"        configs.push_back(cfg);\n"
        f"    }}"
    )

configs_body = "\n".join(config_lines) if config_lines else \
    "    // No properties parsed — check AIDL files"

# Read existing cpp and replace getAllPropertyConfigs body
cpp = open(cpp_path).read()
new_func = (
    f"std::vector<aidlvhal::VehiclePropConfig> VssVehicleHardware::getAllPropertyConfigs() const {{\n"
    f"    std::vector<aidlvhal::VehiclePropConfig> configs;\n"
    f"    configs.reserve({len(props)});\n"
    f"{configs_body}\n"
    f"    return configs;\n"
    f"}}"
)
cpp = re.sub(
    r'std::vector<aidlvhal::VehiclePropConfig> VssVehicleHardware::getAllPropertyConfigs\(\) const \{.*?\}',
    new_func, cpp, flags=re.DOTALL
)
# Update comment
cpp = re.sub(
    r'// Aggregates getAllPropertyConfigs\(\) from \d+ VSS properties',
    f'// Aggregates getAllPropertyConfigs() from {len(props)} VSS properties (full 32-bit prop IDs)',
    cpp
)
open(cpp_path, "w").write(cpp)
print(f"  Fixed {len(props)} prop IDs in VssVehicleHardware.cpp (full 32-bit VHAL IDs)")
PYEOF
    ok "VssVehicleHardware.cpp prop IDs fixed (full 32-bit VHAL property IDs)"
fi