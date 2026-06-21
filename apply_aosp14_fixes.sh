#!/bin/bash
# ═══════════════════════════════════════════════════════════════
# apply_aosp14_fixes.sh
# Android 14 AOSP integration for C3/C4 generated VHAL code.
#
# Copy + patch-tree ONLY. Does NOT build and does NOT relaunch Cuttlefish.
# After this script, build/launch manually (see README Step 6 "build the triple"):
#   rm -f $ANDROID_PRODUCT_OUT/super.img $ANDROID_PRODUCT_OUT/vendor.img
#   m selinux_policy && m vendorimage && m superimage && m vbmetaimage
#   pkill -9 -f crosvm; pkill -9 -f run_cvd; cvd reset -y
#   rm -f ~/cuttlefish/instances/cvd-1/*.img
#   launch_cvd --daemon
#
# This script now also does everything the old update_vss_selinux.sh did
# (device-tree SELinux + LOCAL_VHAL_PRODUCT_PACKAGE swap), so that helper
# is obsolete and can be deleted. There is exactly ONE source of SELinux
# policy now: the Cuttlefish device tree. No core system/sepolicy writes.
# ═══════════════════════════════════════════════════════════════

set -euo pipefail

OUT="${1:?Usage: $0 <output_dir> [aosp_root]  e.g. ~/output_c4}"
AOSP_ROOT="${2:-${ANDROID_BUILD_TOP:-$(pwd)}}"

RED='\033[0;31m'; GREEN='\033[0;32m'
YELLOW='\033[1;33m'; NC='\033[0m'
ok()   { echo -e "  ${GREEN}✓${NC} $1"; }
warn() { echo -e "  ${YELLOW}⚠${NC} $1"; }
fail() { echo -e "  ${RED}✗${NC} $1"; exit 1; }

echo "═══════════════════════════════════════════════════════════"
echo "  Android 14 AOSP Integration (copy + patch-tree, no build)"
echo "═══════════════════════════════════════════════════════════"
echo "  Output : $OUT"
echo "  AOSP   : $AOSP_ROOT"
echo ""

[ -d "$OUT" ]              || fail "Output dir not found: $OUT"
[ -d "$AOSP_ROOT/build" ]  || fail "AOSP root invalid: $AOSP_ROOT"

# ── VSS service binary name (must match Android.bp cc_binary name) ──
VHAL_BINARY='android.hardware.automotive.vehicle@V3-vss-service'
VHAL_BINARY_RE='android\.hardware\.automotive\.vehicle@V3-vss-service'

# ── Destination paths ─────────────────────────────────────────
AIDL_DIR="$AOSP_ROOT/hardware/interfaces/automotive/vehicle/aidl/android/hardware/automotive/vehicle"
VSS_DIR="$AOSP_ROOT/hardware/interfaces/automotive/vehicle/aidl/impl/vss"
FCM_EXCLUDE="$AOSP_ROOT/hardware/interfaces/compatibility_matrices/exclude/fcm_exclude.cpp"
VHAL_BP="$AOSP_ROOT/hardware/interfaces/automotive/vehicle/aidl/impl/vhal/Android.bp"
VHAL_DEFAULT_XML_OUT="$AOSP_ROOT/out/target/product/vsoc_x86_64_only/vendor/etc/vintf/manifest/vhal-default-service.xml"

# SELinux now lives in the Cuttlefish DEVICE TREE (single source of truth).
CF_SEPOL_DIR="$AOSP_ROOT/device/google/cuttlefish/shared/sepolicy/vendor"
DEVICE_VENDOR_MK="$AOSP_ROOT/device/google/cuttlefish/shared/auto/device_vendor.mk"
CORE_SEPOL_DIR="$AOSP_ROOT/system/sepolicy/vendor"   # only used to REMOVE stale files

mkdir -p "$AIDL_DIR" "$VSS_DIR" "$CF_SEPOL_DIR"

# Append a line to a file only if not already present (idempotent).
append_once() {
    local line="$1" file="$2"
    touch "$file"
    grep -qF -- "$line" "$file" || echo "$line" >> "$file"
}

# ═══════════════════════════════════════════════════════════════
# [0/5] Platform build fixes (idempotent)
# ═══════════════════════════════════════════════════════════════
echo "[0/5] Applying platform build fixes..."

CONT_TESTS="$AOSP_ROOT/platform_testing/build/tasks/continuous_native_tests.mk"
if [ ! -f "$CONT_TESTS" ] || ! grep -q "Disabled" "$CONT_TESTS" 2>/dev/null; then
    echo "# Disabled to avoid sv_2d_session_tests build failure in custom AAOS builds" > "$CONT_TESTS"
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
# [1/5] Copy AIDL files
# ═══════════════════════════════════════════════════════════════
echo ""
echo "[1/5] Copying AIDL files..."
COUNT=0
for f in "$OUT"/hardware/interfaces/automotive/vehicle/aidl/android/hardware/automotive/vehicle/*.aidl; do
    [ -f "$f" ] || continue
    cp "$f" "$AIDL_DIR/" && ok "AIDL: $(basename "$f")"
    COUNT=$((COUNT + 1))
done
[ $COUNT -eq 0 ] && warn "No AIDL files found"

# ═══════════════════════════════════════════════════════════════
# [2/5] Copy C++ + VssGlueAgent artifacts into aidl/impl/vss/
# The pipeline (VssGlueAgent) MUST have produced Android.bp here.
# This script only copies it — it does not author Android.bp.
# ═══════════════════════════════════════════════════════════════
echo ""
echo "[2/5] Copying C++ and glue artifacts into $VSS_DIR..."
COUNT=0
VSS_GLUE_SRC="$OUT/hardware/interfaces/automotive/vehicle/aidl/impl/vss"
if [ -d "$VSS_GLUE_SRC" ]; then
    for f in "$VSS_GLUE_SRC"/*.cpp "$VSS_GLUE_SRC"/*.h \
              "$VSS_GLUE_SRC"/*.bp "$VSS_GLUE_SRC"/*.xml "$VSS_GLUE_SRC"/*.rc; do
        [ -f "$f" ] || continue
        cp "$f" "$VSS_DIR/" && ok "Glue: $(basename "$f")"
        COUNT=$((COUNT + 1))
    done
else
    warn "VssGlueAgent output not found: $VSS_GLUE_SRC"
    warn "Re-run pipeline with VssGlueAgent enabled"
fi

for f in "$OUT"/hardware/interfaces/automotive/vehicle/impl/VehicleHalService*.cpp \
          "$OUT"/hardware/interfaces/automotive/vehicle/impl/VehicleHalService*.h; do
    [ -f "$f" ] || continue
    cp "$f" "$VSS_DIR/" && ok "Domain C++: $(basename "$f")"
    COUNT=$((COUNT + 1))
done

[ $COUNT -eq 0 ] && warn "No C++ files found"

if [ ! -f "$VSS_DIR/Android.bp" ]; then
    warn "No Android.bp in $VSS_DIR — pipeline did not emit one; VSS module will NOT build"
fi

# ═══════════════════════════════════════════════════════════════
# [3/5] SELinux — Cuttlefish DEVICE TREE only (single source of truth)
#   - remove any stale core-tree copy (cause of 'Duplicate declaration')
#   - write hal_vehicle_vss.te (overwrite — this script owns it)
#   - append file_contexts label (idempotent)
# ═══════════════════════════════════════════════════════════════
echo ""
echo "[3/5] Writing SELinux policy into Cuttlefish device tree..."

# Remove stale core-tree policy from older runs / older script versions.
for stale in "$CORE_SEPOL_DIR/hal_vehicle_vss.te" "$CORE_SEPOL_DIR/vehicle_hal_vss.te"; do
    if [ -f "$stale" ]; then
        rm -f "$stale" && warn "Removed stale core-tree policy: $stale"
    fi
done
for fc in "$CORE_SEPOL_DIR/file_contexts" "$CORE_SEPOL_DIR/file_contexts_vss"; do
    if [ -f "$fc" ] && grep -q 'hal_vehicle_vss' "$fc" 2>/dev/null; then
        sed -i '/hal_vehicle_vss/d' "$fc" && warn "Cleaned stale label from $fc"
    fi
done

# Optional permissive mode for denial-gathering: pass --permissive or set VSS_PERMISSIVE=1.
PERMISSIVE_LINE=""
case "${*:-}" in *--permissive*) VSS_PERMISSIVE=1 ;; esac
if [ "${VSS_PERMISSIVE:-0}" = "1" ]; then
    PERMISSIVE_LINE="permissive hal_vehicle_vss;"
    warn "PERMISSIVE mode enabled for hal_vehicle_vss — DEBUG ONLY, disable before final results"
fi

cat > "$CF_SEPOL_DIR/hal_vehicle_vss.te" << EOF
type hal_vehicle_vss, domain;
type hal_vehicle_vss_exec, exec_type, vendor_file_type, file_type;

init_daemon_domain(hal_vehicle_vss)
hal_server_domain(hal_vehicle_vss, hal_vehicle)

allow hal_vehicle_vss self:process { fork sigchld };
allow hal_vehicle_vss hal_vehicle_default:process signal;

# IVehicle/default is labelled hal_vehicle_service (NOT vehicle_service) on AOSP 14.
allow hal_vehicle_vss hal_vehicle_service:service_manager add;

# carwatchdog client (the auto/sepolicy/vhal ifeq block is bypassed by the VHAL swap)
carwatchdog_client_domain(hal_vehicle_vss)
binder_use(hal_vehicle_vss)

allow hal_vehicle_vss vendor_configs_file:dir search;
allow hal_vehicle_vss vendor_configs_file:file { read getattr open };
${PERMISSIVE_LINE}
EOF
ok "Wrote $CF_SEPOL_DIR/hal_vehicle_vss.te"

append_once "/vendor/bin/hw/${VHAL_BINARY_RE} u:object_r:hal_vehicle_vss_exec:s0" \
            "$CF_SEPOL_DIR/file_contexts"
ok "Labelled $VHAL_BINARY in device-tree file_contexts"

# ═══════════════════════════════════════════════════════════════
# [4/5] Select VSS as the Cuttlefish VHAL (LOCAL_VHAL_PRODUCT_PACKAGE)
#   Cuttlefish picks its VHAL via this make variable, NOT PRODUCT_PACKAGES.
# ═══════════════════════════════════════════════════════════════
echo ""
echo "[4/5] Pointing LOCAL_VHAL_PRODUCT_PACKAGE at VSS..."
if [ -f "$DEVICE_VENDOR_MK" ]; then
    if grep -q "LOCAL_VHAL_PRODUCT_PACKAGE := ${VHAL_BINARY}" "$DEVICE_VENDOR_MK"; then
        ok "device_vendor.mk already targets VSS"
    elif grep -q 'LOCAL_VHAL_PRODUCT_PACKAGE := android.hardware.automotive.vehicle@V3-emulator-service' "$DEVICE_VENDOR_MK"; then
        cp "$DEVICE_VENDOR_MK" "${DEVICE_VENDOR_MK}.bak"
        sed -i "s|LOCAL_VHAL_PRODUCT_PACKAGE := android.hardware.automotive.vehicle@V3-emulator-service|LOCAL_VHAL_PRODUCT_PACKAGE := ${VHAL_BINARY}|" \
            "$DEVICE_VENDOR_MK"
        ok "Swapped emulator → VSS in device_vendor.mk (backup: .bak)"
    else
        warn "Expected emulator-service line not found in device_vendor.mk"
        warn "Set manually: LOCAL_VHAL_PRODUCT_PACKAGE := ${VHAL_BINARY}"
    fi
else
    warn "device_vendor.mk not found: $DEVICE_VENDOR_MK"
fi

# ═══════════════════════════════════════════════════════════════
# [5/5] AOSP 14 one-time fixes (idempotent)
# ═══════════════════════════════════════════════════════════════
echo ""
echo "[5/5] Applying AOSP 14 one-time fixes..."

if [ -f "$VHAL_BP" ] && grep -q "vintf_fragments.*vhal-default-service" "$VHAL_BP"; then
    sed -i '/vintf_fragments.*vhal-default-service.xml/d' "$VHAL_BP"
    ok "Removed conflicting vintf_fragments from vhal/Android.bp"
else
    ok "vhal/Android.bp already clean"
fi

if [ -f "$VHAL_DEFAULT_XML_OUT" ]; then
    rm -f "$VHAL_DEFAULT_XML_OUT"
    ok "Removed stale vhal-default-service.xml from out/"
else
    ok "No stale vhal-default-service.xml in out/"
fi

AIDL_BP_FILE="$AOSP_ROOT/hardware/interfaces/automotive/vehicle/aidl/Android.bp"
if [ -f "$AIDL_BP_FILE" ]; then
    sed -i 's/frozen: true,/frozen: false,/' "$AIDL_BP_FILE"
    ok "AIDL interface unfrozen"
else
    warn "AIDL Android.bp not found — skipping"
fi

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
echo "  Tree patched. Now BUILD + LAUNCH manually:"
echo ""
echo "    m android.hardware.automotive.vehicle-update-api"
echo "    rm -f \$ANDROID_PRODUCT_OUT/super.img \$ANDROID_PRODUCT_OUT/vendor.img"
echo "    m selinux_policy && m vendorimage && m superimage && m vbmetaimage"
echo "    pkill -9 -f crosvm; pkill -9 -f run_cvd; cvd reset -y"
echo "    rm -f ~/cuttlefish/instances/cvd-1/*.img"
echo "    launch_cvd --daemon"
echo "    ./post_boot_check.sh"
echo "═══════════════════════════════════════════════════════════"