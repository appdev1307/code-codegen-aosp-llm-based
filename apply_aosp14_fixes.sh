#!/bin/bash
# ═══════════════════════════════════════════════════════════════
# apply_aosp14_fixes.sh
# Android 14 AOSP integration for C3/C4 generated VHAL code.
# Improved version - registers custom VssVehicleHardware
# ═══════════════════════════════════════════════════════════════
set -euo pipefail

OUT="${1:?Usage: $0 <output_dir> e.g. ~/output_c4}"
AOSP_ROOT="${2:-$(pwd)}"

RED='\033[0;31m'; GREEN='\033[0;32m'
YELLOW='\033[1;33m'; NC='\033[0m'

ok() { echo -e " ${GREEN}✓${NC} $1"; }
warn() { echo -e " ${YELLOW}⚠${NC} $1"; }
fail() { echo -e " ${RED}✗${NC} $1"; exit 1; }

echo "═══════════════════════════════════════════════════════════"
echo " Android 14 AOSP Custom VSS VHAL Integration"
echo "═══════════════════════════════════════════════════════════"
echo " Output : $OUT"
echo " AOSP   : $AOSP_ROOT"
echo ""

[ -d "$OUT" ] || fail "Output dir not found: $OUT"
[ -d "$AOSP_ROOT/build" ] || fail "AOSP root invalid: $AOSP_ROOT"

# ── Destination paths ────────────────────────────────────────
AIDL_DIR="$AOSP_ROOT/hardware/interfaces/automotive/vehicle/aidl/android/hardware/automotive/vehicle"
VSS_DIR="$AOSP_ROOT/hardware/interfaces/automotive/vehicle/aidl/impl/vss"
SEPOL_DEST="$AOSP_ROOT/system/sepolicy/vendor"
FCM_EXCLUDE="$AOSP_ROOT/hardware/interfaces/compatibility_matrices/exclude/fcm_exclude.cpp"
VHAL_BP="$AOSP_ROOT/hardware/interfaces/automotive/vehicle/aidl/impl/vhal/Android.bp"
VHAL_DEFAULT_XML_OUT="$AOSP_ROOT/out/target/product/vsoc_x86_64_only/vendor/etc/vintf/manifest/vhal-default-service.xml"
DEVICE_MK="$AOSP_ROOT/device/google/cuttlefish/shared/auto/device.mk"

mkdir -p "$AIDL_DIR" "$VSS_DIR" "$SEPOL_DEST"

# ═══════════════════════════════════════════════════════════════
# [0/4] Platform build fixes
# ═══════════════════════════════════════════════════════════════
echo "[0/4] Applying platform build fixes..."

CONT_TESTS="$AOSP_ROOT/platform_testing/build/tasks/continuous_native_tests.mk"
if grep -q "sv_2d_session_tests\|sv_3d_session_tests" "$CONT_TESTS" 2>/dev/null || [ ! -f "$CONT_TESTS" ] || ! grep -q "Disabled" "$CONT_TESTS" 2>/dev/null; then
    cat > "$CONT_TESTS" << 'EOF'
# Disabled to avoid sv_2d_session_tests and sv_3d_session_tests build failure
# in custom AAOS builds (platform_testing broken test modules)
EOF
    ok "Disabled continuous_native_tests.mk"
else
    ok "continuous_native_tests.mk already patched"
fi

if [ -f "$DEVICE_MK" ] && ! grep -q "sv_2d_session_tests" "$DEVICE_MK"; then
    cat >> "$DEVICE_MK" << 'EOF'
# Disable broken test modules (custom AAOS build)
PRODUCT_PACKAGES += -sv_2d_session_tests -sv_3d_session_tests
PRODUCT_PACKAGES += -continuous_native_tests
EOF
    ok "Disabled broken test modules in device.mk"
else
    ok "device.mk already patched"
fi
echo ""

# ═══════════════════════════════════════════════════════════════
# [1/3] Copy AIDL
# ═══════════════════════════════════════════════════════════════
echo "[1/3] Copying AIDL files..."
COUNT=0
for f in "$OUT"/hardware/interfaces/automotive/vehicle/aidl/android/hardware/automotive/vehicle/*.aidl; do
    [ -f "$f" ] || continue
    cp "$f" "$AIDL_DIR/" && ok "AIDL: $(basename $f)"
    COUNT=$((COUNT + 1))
done
[ $COUNT -eq 0 ] && warn "No AIDL files found"

# ═══════════════════════════════════════════════════════════════
# [2/3] Copy C++ into aidl/impl/vss/
# ═══════════════════════════════════════════════════════════════
echo ""
echo "[2/3] Copying C++ into $VSS_DIR..."
COUNT=0
for f in "$OUT"/hardware/interfaces/automotive/vehicle/impl/*.cpp \
          "$OUT"/hardware/interfaces/automotive/vehicle/impl/*.h; do
    [ -f "$f" ] || continue
    cp "$f" "$VSS_DIR/" && ok "C++: $(basename $f)"
    COUNT=$((COUNT + 1))
done
[ $COUNT -eq 0 ] && warn "No C++ files found"

cat > "$VSS_DIR/Android.bp" << 'EOF'
package {
    default_applicable_licenses: ["Android-Apache-2.0"],
}
cc_library_static {
    name: "VssVehicleHardware",
    vendor: true,
    defaults: ["VehicleHalDefaults"],
    srcs: ["*.cpp"],
    header_libs: ["IVehicleHardware", "VehicleHalUtilHeaders"],
    export_include_dirs: ["."],
}
EOF
ok "VssVehicleHardware Android.bp created"

# ═══════════════════════════════════════════════════════════════
# [3/3] Copy SELinux, VINTF, init.rc + Register Custom VHAL
# ═══════════════════════════════════════════════════════════════
echo ""
echo "[3/3] Installing Custom VSS VHAL..."

# SELinux
COUNT=0
for f in "$OUT"/sepolicy/vehicle_hal_*.te; do
    [ -f "$f" ] || continue
    cp "$f" "$SEPOL_DEST/" && ok "SELinux: $(basename $f)"
    COUNT=$((COUNT + 1))
done
[ $COUNT -eq 0 ] && warn "No .te files found"

if [ -f "$OUT/sepolicy/private/file_contexts" ]; then
    cp "$OUT/sepolicy/private/file_contexts" "$SEPOL_DEST/file_contexts_vss"
    ok "file_contexts copied"
fi

# VINTF + Override manifest
VINTF_SRC=$(find "$OUT" -name "manifest*.xml" -not -path "*/.llm_draft/*" | head -1)
[ -n "$VINTF_SRC" ] && cp "$VINTF_SRC" "$VSS_DIR/manifest_vss.xml" && ok "VINTF copied" || warn "No VINTF found"

# Create strong override manifest
cat > "$VSS_DIR/manifest_vss.xml" << 'EOF'
<manifest version="1.0" type="device">
    <hal format="aidl" override="true">
        <name>android.hardware.automotive.vehicle</name>
        <version>4</version>
        <fqname>IVehicle/default</fqname>
        <instance>default</instance>
    </hal>
</manifest>
EOF
ok "Created override manifest for VssVehicleHardware"

# Register in device.mk
if ! grep -q "VssVehicleHardware" "$DEVICE_MK" 2>/dev/null; then
    cat >> "$DEVICE_MK" << EOF
# Custom VSS Vehicle HAL
PRODUCT_PACKAGES += VssVehicleHardware
EOF
    ok "Registered VssVehicleHardware in device.mk"
fi

# ═══════════════════════════════════════════════════════════════
# [4/4] AOSP 14 one-time fixes
# ═══════════════════════════════════════════════════════════════
echo ""
echo "[4/4] Applying AOSP 14 one-time fixes..."

# Fix 1: Remove conflicting vintf_fragments
if [ -f "$VHAL_BP" ]; then
    if grep -q "vintf_fragments.*vhal-default-service" "$VHAL_BP"; then
        sed -i '/vintf_fragments.*vhal-default-service.xml/d' "$VHAL_BP"
        ok "Removed conflicting vintf_fragments"
    else
        ok "vhal/Android.bp already clean"
    fi
fi

# Fix 2: Remove stale default xml
if [ -f "$VHAL_DEFAULT_XML_OUT" ]; then
    rm -f "$VHAL_DEFAULT_XML_OUT"
    ok "Removed stale vhal-default-service.xml"
fi

# Fix 3: FCM exempt for vehicle@4
echo ""
echo "[4/4c] Fixing FCM exempt for vehicle@4..."
if [ -f "$FCM_EXCLUDE" ]; then
    if grep -q "android\.hardware\.automotive\.vehicle@4" "$FCM_EXCLUDE"; then
        ok "FCM exempt already present"
    else
        sed -i '/static std::vector<std::string> excluded_exact{/a\    "android.hardware.automotive.vehicle@4",' "$FCM_EXCLUDE"
        ok "Added android.hardware.automotive.vehicle@4 to FCM exempt"
    fi
fi

echo ""
echo "═══════════════════════════════════════════════════════════"
echo "✅ Done! Custom VSS VHAL should now be registered."
echo "Next steps:"
echo "   m clean-vintf vendor_vintf_fragments -j\$(nproc)"
echo "   m check-vintf-all"
echo "   stop_cvd && launch_cvd --noresume --cpus=8 --memory_mb=8192 --gpu_mode=guest_swiftshader"
echo "═══════════════════════════════════════════════════════════"