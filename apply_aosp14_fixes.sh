#!/bin/bash
# ═══════════════════════════════════════════════════════════════
# apply_aosp14_fixes.sh
# Android 14 AOSP integration for C3/C4 generated VHAL code.
# Copy-only — no patching, no manual registration.
# Soong auto-discovers Android.bp under aidl/impl/vss/.
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

# ── Destination paths ────────────────────────────────────────
AIDL_DIR="$AOSP_ROOT/hardware/interfaces/automotive/vehicle/aidl/android/hardware/automotive/vehicle"
VSS_DIR="$AOSP_ROOT/hardware/interfaces/automotive/vehicle/aidl/impl/vss"
SEPOL_DEST="$AOSP_ROOT/system/sepolicy/vendor"
FCM_EXCLUDE="$AOSP_ROOT/hardware/interfaces/compatibility_matrices/exclude/fcm_exclude.cpp"
VHAL_BP="$AOSP_ROOT/hardware/interfaces/automotive/vehicle/aidl/impl/vhal/Android.bp"
VHAL_DEFAULT_XML_OUT="$AOSP_ROOT/out/target/product/vsoc_x86_64_only/vendor/etc/vintf/manifest/vhal-default-service.xml"

mkdir -p "$AIDL_DIR" "$VSS_DIR" "$SEPOL_DEST"

# ═══════════════════════════════════════════════════════════════
# [1/3] Copy AIDL
# ═══════════════════════════════════════════════════════════════
echo "[1/3] Copying AIDL files..."
COUNT=0
for f in "$OUT"/hardware/interfaces/automotive/vehicle/aidl/android/hardware/automotive/vehicle/*.aidl; do
    [ -f "$f" ] || continue
    cp "$f" "$AIDL_DIR/" && ok "AIDL: $(basename $f)"
    ((COUNT++))
done
[ $COUNT -eq 0 ] && warn "No AIDL files found"

# ═══════════════════════════════════════════════════════════════
# [2/3] Copy C++ into aidl/impl/vss/
# Soong auto-discovers this Android.bp — no registration needed
# ═══════════════════════════════════════════════════════════════
echo ""
echo "[2/3] Copying C++ into $VSS_DIR..."
COUNT=0
for f in "$OUT"/hardware/interfaces/automotive/vehicle/impl/*.cpp \
          "$OUT"/hardware/interfaces/automotive/vehicle/impl/*.h; do
    [ -f "$f" ] || continue
    cp "$f" "$VSS_DIR/" && ok "C++: $(basename $f)"
    ((COUNT++))
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
ok "VssVehicleHardware Android.bp (auto-discovered by Soong)"

# ═══════════════════════════════════════════════════════════════
# [3/3] Copy SELinux, VINTF, init.rc — as-is, no patching
# ═══════════════════════════════════════════════════════════════
echo ""
echo "[3/3] Copying SELinux / VINTF / init.rc..."

COUNT=0
for f in "$OUT"/sepolicy/vehicle_hal_*.te; do
    [ -f "$f" ] || continue
    cp "$f" "$SEPOL_DEST/" && ok "SELinux: $(basename $f)"
    ((COUNT++))
done
[ $COUNT -eq 0 ] && warn "No .te files found"

if [ -f "$OUT/sepolicy/private/file_contexts" ]; then
    cp "$OUT/sepolicy/private/file_contexts" "$SEPOL_DEST/file_contexts_vss"
    ok "file_contexts"
fi

VINTF_SRC=$(find "$OUT" -name "manifest*.xml" -not -path "*/.llm_draft/*" | head -1)
[ -n "$VINTF_SRC" ] && cp "$VINTF_SRC" "$VSS_DIR/manifest_vss.xml" \
    && ok "VINTF: $(basename $VINTF_SRC)" || warn "No VINTF manifest found"

RC_SRC=$(find "$OUT" -name "*.rc" -not -path "*/.llm_draft/*" | head -1)
[ -n "$RC_SRC" ] && cp "$RC_SRC" "$VSS_DIR/$(basename $RC_SRC)" \
    && ok "init.rc: $(basename $RC_SRC)" || warn "No init.rc found"

# ═══════════════════════════════════════════════════════════════
# [4/4] AOSP 14 one-time fixes (idempotent)
# ═══════════════════════════════════════════════════════════════
echo ""
echo "[4/4] Applying AOSP 14 one-time fixes..."

# Fix 1: Add android.hardware.automotive.vehicle@4 to FCM exempt list
# Required when adding new AIDL types-only package (VehiclePropertyAdas)
if [ -f "$FCM_EXCLUDE" ]; then
    if grep -q "automotive.vehicle@4" "$FCM_EXCLUDE"; then
        ok "FCM exempt: vehicle@4 already present"
    else
        sed -i '/static std::vector<std::string> excluded_exact{/a\            "android.hardware.automotive.vehicle@4",' \
            "$FCM_EXCLUDE"
        ok "FCM exempt: added android.hardware.automotive.vehicle@4"
    fi
else
    warn "fcm_exclude.cpp not found: $FCM_EXCLUDE"
fi

# Fix 2: Remove vintf_fragments from vhal/Android.bp to avoid
# conflict between vhal-default-service.xml and vhal-emulator-service.xml
# (both register IVehicle/default@3 — Cuttlefish uses emulator service)
if [ -f "$VHAL_BP" ]; then
    if grep -q "vintf_fragments.*vhal-default-service" "$VHAL_BP"; then
        sed -i '/vintf_fragments.*vhal-default-service.xml/d' "$VHAL_BP"
        ok "Removed conflicting vintf_fragments from vhal/Android.bp"
    else
        ok "vhal/Android.bp already clean"
    fi
else
    warn "vhal/Android.bp not found: $VHAL_BP"
fi

# Fix 3: Remove stale vhal-default-service.xml from out/ if present
if [ -f "$VHAL_DEFAULT_XML_OUT" ]; then
    rm -f "$VHAL_DEFAULT_XML_OUT"
    ok "Removed stale vhal-default-service.xml from out/"
else
    ok "No stale vhal-default-service.xml in out/"
fi

echo ""
echo "═══════════════════════════════════════════════════════════"
echo "  Done — Soong will auto-discover aidl/impl/vss/"
echo "═══════════════════════════════════════════════════════════"
echo "  m -j\$(nproc) 2>&1 | tee ~/build_full_c4.log"
echo "═══════════════════════════════════════════════════════════"