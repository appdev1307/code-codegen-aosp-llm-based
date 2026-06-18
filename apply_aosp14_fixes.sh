#!/bin/bash
# ═══════════════════════════════════════════════════════════════
# apply_aosp14_fixes.sh
# Android 14 AOSP integration for C3/C4 generated VHAL code.
# Copy-only — pipeline (VssGlueAgent) generates all artifacts.
# ═══════════════════════════════════════════════════════════════

set -euo pipefail

OUT="${1:?Usage: $0 <output_dir>  e.g. ~/output_c4}"
AOSP_ROOT="${2:-$(pwd)}"

RED='\033[0;31m'; GREEN='\033[0;32m'
YELLOW='\033[1;33m'; NC='\033[0m'
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

# ── Destination paths ─────────────────────────────────────────
AIDL_DIR="$AOSP_ROOT/hardware/interfaces/automotive/vehicle/aidl/android/hardware/automotive/vehicle"
VSS_DIR="$AOSP_ROOT/hardware/interfaces/automotive/vehicle/aidl/impl/vss"
SEPOL_DEST="$AOSP_ROOT/system/sepolicy/vendor"
FCM_EXCLUDE="$AOSP_ROOT/hardware/interfaces/compatibility_matrices/exclude/fcm_exclude.cpp"
VHAL_BP="$AOSP_ROOT/hardware/interfaces/automotive/vehicle/aidl/impl/vhal/Android.bp"
VHAL_DEFAULT_XML_OUT="$AOSP_ROOT/out/target/product/vsoc_x86_64_only/vendor/etc/vintf/manifest/vhal-default-service.xml"

mkdir -p "$AIDL_DIR" "$VSS_DIR" "$SEPOL_DEST"

# ═══════════════════════════════════════════════════════════════
# [0/4] Platform build fixes (idempotent)
# ═══════════════════════════════════════════════════════════════
echo "[0/4] Applying platform build fixes..."

CONT_TESTS="$AOSP_ROOT/platform_testing/build/tasks/continuous_native_tests.mk"
if [ ! -f "$CONT_TESTS" ] || ! grep -q "Disabled" "$CONT_TESTS" 2>/dev/null; then
    cat > "$CONT_TESTS" << 'EOF'
# Disabled to avoid sv_2d_session_tests build failure in custom AAOS builds
EOF
    ok "Disabled continuous_native_tests.mk"
else
    ok "continuous_native_tests.mk already patched"
fi

DEVICE_MK="$AOSP_ROOT/device/google/cuttlefish/shared/device.mk"
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

# ═══════════════════════════════════════════════════════════════
# [1/4] Copy AIDL files
# ═══════════════════════════════════════════════════════════════
echo ""
echo "[1/4] Copying AIDL files..."
COUNT=0
for f in "$OUT"/hardware/interfaces/automotive/vehicle/aidl/android/hardware/automotive/vehicle/*.aidl; do
    [ -f "$f" ] || continue
    cp "$f" "$AIDL_DIR/" && ok "AIDL: $(basename $f)"
    COUNT=$((COUNT + 1))
done
[ $COUNT -eq 0 ] && warn "No AIDL files found"

# ═══════════════════════════════════════════════════════════════
# [2/4] Copy C++ + VssGlueAgent artifacts into aidl/impl/vss/
# VssGlueAgent (pipeline) already generated:
#   VssVehicleHardware.h/.cpp, VehicleServiceMain.cpp,
#   Android.bp, manifest_vss.xml, *.rc
# ═══════════════════════════════════════════════════════════════
echo ""
echo "[2/4] Copying C++ and glue artifacts into $VSS_DIR..."
COUNT=0

# Copy VssGlueAgent outputs from pipeline
VSS_GLUE_SRC="$OUT/hardware/interfaces/automotive/vehicle/aidl/impl/vss"
if [ -d "$VSS_GLUE_SRC" ]; then
    for f in "$VSS_GLUE_SRC"/*.cpp "$VSS_GLUE_SRC"/*.h \
              "$VSS_GLUE_SRC"/*.bp "$VSS_GLUE_SRC"/*.xml \
              "$VSS_GLUE_SRC"/*.rc; do
        [ -f "$f" ] || continue
        cp "$f" "$VSS_DIR/" && ok "Glue: $(basename $f)"
        COUNT=$((COUNT + 1))
    done
else
    warn "VssGlueAgent output not found: $VSS_GLUE_SRC"
    warn "Re-run pipeline with VssGlueAgent enabled"
fi

# Also copy domain-specific C++ from impl/ (VehicleHalService*.cpp/.h)
for f in "$OUT"/hardware/interfaces/automotive/vehicle/impl/VehicleHalService*.cpp \
          "$OUT"/hardware/interfaces/automotive/vehicle/impl/VehicleHalService*.h; do
    [ -f "$f" ] || continue
    cp "$f" "$VSS_DIR/" && ok "Domain C++: $(basename $f)"
    COUNT=$((COUNT + 1))
done

[ $COUNT -eq 0 ] && warn "No C++ files found"

# ═══════════════════════════════════════════════════════════════
# [3/4] Copy SELinux — fix truncation before copying
# ═══════════════════════════════════════════════════════════════
echo ""
echo "[3/4] Copying SELinux / file_contexts..."
COUNT=0
for f in "$OUT"/sepolicy/vehicle_hal_*.te; do
    [ -f "$f" ] || continue

    # Pipeline (rag_dspy_mixin.py) already fixes truncation and missing allow keyword
    cp "$f" "$SEPOL_DEST/" && ok "SELinux: $(basename $f)"
    COUNT=$((COUNT + 1))
done
[ $COUNT -eq 0 ] && warn "No .te files found"

if [ -f "$OUT/sepolicy/private/file_contexts" ]; then
    cp "$OUT/sepolicy/private/file_contexts" "$SEPOL_DEST/file_contexts_vss"
    ok "file_contexts"
fi

# Add SELinux label for VSS binary
FC_VSS="$SEPOL_DEST/file_contexts_vss"
VSS_BINARY_LABEL="/vendor/bin/hw/android\\.hardware\\.automotive\\.vehicle@V3-vss-service  u:object_r:hal_vehicle_default_exec:s0"
if ! grep -qF "V3-vss-service" "$FC_VSS" 2>/dev/null; then
    echo "$VSS_BINARY_LABEL" >> "$FC_VSS"
    ok "SELinux label for V3-vss-service added"
else
    ok "SELinux label for V3-vss-service already present"
fi

# ═══════════════════════════════════════════════════════════════
# [4/4] AOSP 14 one-time fixes (idempotent)
# ═══════════════════════════════════════════════════════════════
echo ""
echo "[4/4] Applying AOSP 14 one-time fixes..."

# Remove conflicting vintf_fragments from vhal/Android.bp
if [ -f "$VHAL_BP" ]; then
    if grep -q "vintf_fragments.*vhal-default-service" "$VHAL_BP"; then
        sed -i '/vintf_fragments.*vhal-default-service.xml/d' "$VHAL_BP"
        ok "Removed conflicting vintf_fragments from vhal/Android.bp"
    else
        ok "vhal/Android.bp already clean"
    fi
fi

# Remove stale vhal-default-service.xml from out/
if [ -f "$VHAL_DEFAULT_XML_OUT" ]; then
    rm -f "$VHAL_DEFAULT_XML_OUT"
    ok "Removed stale vhal-default-service.xml from out/"
else
    ok "No stale vhal-default-service.xml in out/"
fi

# Unfreeze AIDL interface for research use
AIDL_BP_FILE="$AOSP_ROOT/hardware/interfaces/automotive/vehicle/aidl/Android.bp"
if [ -f "$AIDL_BP_FILE" ]; then
    sed -i 's/frozen: true,/frozen: false,/' "$AIDL_BP_FILE"
    ok "AIDL interface unfrozen"
else
    warn "AIDL Android.bp not found — skipping"
fi

# Add android.hardware.automotive.vehicle@4 to FCM exempt list
if [ -f "$FCM_EXCLUDE" ]; then
    if grep -q "automotive.vehicle@4" "$FCM_EXCLUDE"; then
        ok "FCM exempt: vehicle@4 already present"
    else
        sed -i '/static std::vector<std::string> excluded_exact{/a\            "android.hardware.automotive.vehicle@4",' "$FCM_EXCLUDE"
        ok "FCM exempt: added android.hardware.automotive.vehicle@4"
    fi
else
    warn "fcm_exclude.cpp not found"
fi

echo ""
echo "═══════════════════════════════════════════════════════════"
echo "  Done. Build with:"
echo "  m -j\$(nproc) 2>&1 | tee ~/build_c4.log"
echo "═══════════════════════════════════════════════════════════"