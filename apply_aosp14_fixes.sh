#!/bin/bash
# ═══════════════════════════════════════════════════════════════
# apply_aosp14_fixes.sh
# ═══════════════════════════════════════════════════════════════
# Complete Android 14 AOSP integration for generated VHAL code.
#
# Handles ALL build system integration points:
#   1. Copy generated files to correct AOSP paths
#   2. Register new AIDL in aidl_interface Android.bp srcs
#   3. Handle AIDL API freeze (remove stale hash)
#   4. Fix C++ Android.bp (module name, vendor, deps)
#   5. Fix SELinux (type declarations, file_contexts)
#   6. Fix C++ HIDL→AIDL patterns
#
# Usage:
#   ./apply_aosp14_fixes.sh <output_dir> [aosp_root]
#
# Example:
#   ./apply_aosp14_fixes.sh ~/output_c4 ~/aosp-14-auto
# ═══════════════════════════════════════════════════════════════

set -euo pipefail

OUTPUT_DIR="${1:?Usage: $0 <output_dir> [aosp_root]}"
AOSP_ROOT="${2:-$(pwd)}"

RED='\033[0;31m'; GREEN='\033[0;32m'
YELLOW='\033[1;33m'; BLUE='\033[0;34m'; NC='\033[0m'

ok()   { echo -e "  ${GREEN}✓${NC} $1"; }
warn() { echo -e "  ${YELLOW}⚠${NC} $1"; }
fail() { echo -e "  ${RED}✗${NC} $1"; exit 1; }
info() { echo -e "  ${BLUE}→${NC} $1"; }

echo "═══════════════════════════════════════════════════════════"
echo " Android 14 AOSP Integration — Complete Fix Script"
echo "═══════════════════════════════════════════════════════════"
echo "  Output dir: $OUTPUT_DIR"
echo "  AOSP root:  $AOSP_ROOT"
echo ""

[ -d "$OUTPUT_DIR" ] || fail "Output dir not found: $OUTPUT_DIR"
[ -d "$AOSP_ROOT/build" ] || fail "AOSP root invalid: $AOSP_ROOT"

FIXES=0; WARNINGS=0

# ── AOSP directory layout ────────────────────────────────────
AIDL_BASE="$AOSP_ROOT/hardware/interfaces/automotive/vehicle/aidl"
AIDL_DIR="$AIDL_BASE/android/hardware/automotive/vehicle"
AIDL_BP="$AIDL_BASE/Android.bp"
AIDL_API_DIR="$AIDL_BASE/aidl_api"
SEPOLICY_DIR="$AOSP_ROOT/system/sepolicy/vendor"

# Find the impl directory (varies by AOSP layout)
if [ -d "$AIDL_BASE/impl" ]; then
    IMPL_DIR="$AIDL_BASE/impl"
elif [ -d "$AOSP_ROOT/hardware/interfaces/automotive/vehicle/impl" ]; then
    IMPL_DIR="$AOSP_ROOT/hardware/interfaces/automotive/vehicle/impl"
else
    IMPL_DIR="$AOSP_ROOT/hardware/interfaces/automotive/vehicle/impl"
    mkdir -p "$IMPL_DIR"
fi

mkdir -p "$AIDL_DIR" "$IMPL_DIR" "$SEPOLICY_DIR"


# ═══════════════════════════════════════════════════════════════
# [1/6] Copy generated files to AOSP tree
# ═══════════════════════════════════════════════════════════════
echo "[1/6] Copying generated files to AOSP tree..."

# AIDL
AIDL_COUNT=0
while IFS= read -r f; do
    cp "$f" "$AIDL_DIR/$(basename "$f")"
    ok "AIDL: $(basename "$f")"
    ((AIDL_COUNT++))
done < <(find "$OUTPUT_DIR" -name "*.aidl" -not -path "*/.llm_draft/*" 2>/dev/null)
[ $AIDL_COUNT -eq 0 ] && { warn "No AIDL files found"; ((WARNINGS++)); }

# C++
CPP_SRC=$(find "$OUTPUT_DIR" -name "*.cpp" -not -path "*/.llm_draft/*" | head -1)
if [ -n "$CPP_SRC" ]; then
    cp "$CPP_SRC" "$IMPL_DIR/$(basename "$CPP_SRC")"
    ok "C++: $(basename "$CPP_SRC")"
else
    warn "No C++ file found"; ((WARNINGS++))
fi

# Android.bp (save as .generated — will merge in step 4)
BP_SRC=$(find "$OUTPUT_DIR" -name "Android.bp" -not -path "*/.llm_draft/*" | head -1)
if [ -n "$BP_SRC" ]; then
    cp "$BP_SRC" "$IMPL_DIR/Android.bp.generated"
    ok "Android.bp → Android.bp.generated"
else
    warn "No Android.bp found"; ((WARNINGS++))
fi

# SELinux .te
TE_SRC=$(find "$OUTPUT_DIR" -name "*.te" -not -path "*/.llm_draft/*" | head -1)
if [ -n "$TE_SRC" ]; then
    cp "$TE_SRC" "$SEPOLICY_DIR/$(basename "$TE_SRC")"
    ok "SELinux: $(basename "$TE_SRC")"
else
    warn "No .te file found"; ((WARNINGS++))
fi

# file_contexts
FC_SRC=$(find "$OUTPUT_DIR" -name "file_contexts" -not -path "*/.llm_draft/*" | head -1)
[ -n "$FC_SRC" ] && { cp "$FC_SRC" "$SEPOLICY_DIR/file_contexts.vss"; ok "file_contexts"; }

# VINTF manifest
MANIFEST_SRC=$(find "$OUTPUT_DIR" -name "manifest*.xml" -not -path "*/.llm_draft/*" | head -1)
[ -n "$MANIFEST_SRC" ] && { cp "$MANIFEST_SRC" "$IMPL_DIR/manifest_vss.xml"; ok "VINTF manifest"; }

# init.rc
RC_SRC=$(find "$OUTPUT_DIR" -name "*.rc" -not -path "*/.llm_draft/*" | head -1)
[ -n "$RC_SRC" ] && { cp "$RC_SRC" "$IMPL_DIR/$(basename "$RC_SRC")"; ok "init.rc: $(basename "$RC_SRC")"; }


# ═══════════════════════════════════════════════════════════════
# [2/6] Register AIDL files in aidl_interface Android.bp
# ═══════════════════════════════════════════════════════════════
echo ""
echo "[2/6] Registering new AIDL file(s) in aidl_interface..."

if [ -f "$AIDL_BP" ]; then
    for AIDL_FILE in "$AIDL_DIR"/VehicleProperty{Adas,Vss}*.aidl; do
        [ -f "$AIDL_FILE" ] || continue
        BASENAME=$(basename "$AIDL_FILE")
        REL_PATH="android/hardware/automotive/vehicle/$BASENAME"

        if grep -q "$BASENAME" "$AIDL_BP"; then
            ok "$BASENAME already registered in Android.bp"
        else
            # Find the last .aidl entry in the srcs list and insert after
            LAST_LINE=$(grep -n '\.aidl"' "$AIDL_BP" | tail -1 | cut -d: -f1)
            if [ -n "$LAST_LINE" ]; then
                sed -i "${LAST_LINE}a\\        \"${REL_PATH}\"," "$AIDL_BP"
                ok "Registered $BASENAME in aidl_interface srcs"
                ((FIXES++))
            else
                warn "Could not find srcs in $AIDL_BP — add manually:"
                warn "  \"$REL_PATH\","
                ((WARNINGS++))
            fi
        fi
    done
else
    warn "AIDL Android.bp not found at $AIDL_BP"
    ((WARNINGS++))
fi


# ═══════════════════════════════════════════════════════════════
# [3/6] Handle AIDL API freeze / hash
# ═══════════════════════════════════════════════════════════════
echo ""
echo "[3/6] Handling AIDL API version freeze..."

if [ -f "$AIDL_BP" ]; then
    # Set frozen: false so Soong doesn't check the API hash
    if grep -q "frozen: true" "$AIDL_BP"; then
        sed -i 's/frozen: true/frozen: false/' "$AIDL_BP"
        ok "Set frozen: false in aidl_interface"
        ((FIXES++))
    elif grep -q "frozen:" "$AIDL_BP"; then
        ok "Already frozen: false"
    else
        info "No frozen: field found (may not need it)"
    fi

    # Remove frozen API snapshots — Soong will regenerate
    if [ -d "$AIDL_API_DIR" ]; then
        # Back up first
        HASH_FILE=$(find "$AIDL_API_DIR" -name ".hash" 2>/dev/null | head -1)
        [ -n "$HASH_FILE" ] && cp "$HASH_FILE" "${HASH_FILE}.bak" && ok "Backed up API hash"

        rm -rf "$AIDL_API_DIR"
        ok "Removed aidl_api/ frozen snapshots (Soong will regenerate)"
        ((FIXES++))
    else
        info "No aidl_api/ directory found"
    fi
fi


# ═══════════════════════════════════════════════════════════════
# [4/6] Fix C++ impl Android.bp
# ═══════════════════════════════════════════════════════════════
echo ""
echo "[4/6] Fixing C++ Android.bp..."

GEN_BP="$IMPL_DIR/Android.bp.generated"

if [ -f "$GEN_BP" ]; then
    # Fix module name — must not conflict with existing AOSP
    sed -i 's/name: "android\.hardware\.automotive\.vehicle-service"/name: "vendor.vss.adas-service"/' "$GEN_BP"
    sed -i 's/name: "android\.hardware\.automotive\.vehicle"/name: "vendor.vss.adas-service"/' "$GEN_BP"

    # Ensure vendor: true
    if ! grep -q "vendor: true" "$GEN_BP"; then
        sed -i '/{/a\    vendor: true,' "$GEN_BP"
        ok "Added vendor: true"
        ((FIXES++))
    fi

    # Ensure relative_install_path
    if ! grep -q "relative_install_path" "$GEN_BP"; then
        sed -i '/vendor: true/a\    relative_install_path: "hw",' "$GEN_BP"
        ok "Added relative_install_path"
        ((FIXES++))
    fi

    # Fix HIDL library → AIDL
    sed -i 's/android\.hardware\.automotive\.vehicle@2\.0/android.hardware.automotive.vehicle-V3-ndk/g' "$GEN_BP"

    # Install as the impl Android.bp if none exists
    if [ ! -f "$IMPL_DIR/Android.bp" ]; then
        mv "$GEN_BP" "$IMPL_DIR/Android.bp"
        ok "Installed Android.bp"
    else
        info "Existing Android.bp preserved; generated at Android.bp.generated"
    fi
else
    warn "No generated Android.bp found"
    ((WARNINGS++))
fi


# ═══════════════════════════════════════════════════════════════
# [5/6] Fix SELinux
# ═══════════════════════════════════════════════════════════════
echo ""
echo "[5/6] Fixing SELinux policy..."

TE_FILE=$(find "$SEPOLICY_DIR" -name "vehicle_hal_*.te" 2>/dev/null | head -1)
if [ -n "$TE_FILE" ] && [ -f "$TE_FILE" ]; then
    DOMAIN=$(basename "$TE_FILE" .te)

    # Remove stray braces (common LLM artifact)
    sed -i '/^{$/d; /^}$/d' "$TE_FILE"

    # Add type declarations if missing
    if ! grep -q "^type ${DOMAIN}," "$TE_FILE"; then
        HEADER="# Type declarations for Android 14 Vehicle HAL
type ${DOMAIN}, domain;
type ${DOMAIN}_exec, exec_type, vendor_file_type, file_type;

init_daemon_domain(${DOMAIN})
binder_use(${DOMAIN})
binder_call(${DOMAIN}, system_server)
add_hwservice(${DOMAIN}, hal_vehicle_hwservice)

"
        echo "$HEADER" | cat - "$TE_FILE" > "${TE_FILE}.tmp"
        mv "${TE_FILE}.tmp" "$TE_FILE"
        ok "Added type declarations for ${DOMAIN}"
        ((FIXES++))
    else
        ok "Type declarations already present"
    fi

    # Add file_contexts entry
    FC_FILE="$SEPOLICY_DIR/file_contexts"
    FC_ENTRY="/(vendor|odm)/bin/hw/vendor\.vss\.adas-service    u:object_r:${DOMAIN}_exec:s0"
    if [ -f "$FC_FILE" ]; then
        if ! grep -q "vendor.vss.adas" "$FC_FILE"; then
            echo "" >> "$FC_FILE"
            echo "# VSS ADAS HAL service" >> "$FC_FILE"
            echo "$FC_ENTRY" >> "$FC_FILE"
            ok "Added file_contexts entry"
            ((FIXES++))
        fi
    else
        echo "# VSS ADAS HAL service" > "$FC_FILE"
        echo "$FC_ENTRY" >> "$FC_FILE"
        ok "Created file_contexts"
        ((FIXES++))
    fi
    rm -f "$SEPOLICY_DIR/file_contexts.vss"
else
    warn "No vehicle_hal_*.te found"
    ((WARNINGS++))
fi


# ═══════════════════════════════════════════════════════════════
# [6/6] Fix C++ HIDL → AIDL patterns
# ═══════════════════════════════════════════════════════════════
echo ""
echo "[6/6] Fixing C++ HIDL→AIDL patterns..."

CPP_FILE=$(find "$IMPL_DIR" -name "VehicleHalService*.cpp" 2>/dev/null | head -1)
if [ -n "$CPP_FILE" ] && [ -f "$CPP_FILE" ]; then
    CHANGED=0

    # Replace HIDL includes
    if grep -q "hidl/" "$CPP_FILE"; then
        sed -i 's|#include <hidl/Status.h>|#include <aidl/android/hardware/automotive/vehicle/BnVehicle.h>|' "$CPP_FILE"
        sed -i '/#include <hidl\//d' "$CPP_FILE"
        ok "Fixed HIDL includes"
        ((CHANGED++))
    fi

    # Replace HIDL types with AIDL equivalents
    if grep -q "Return<" "$CPP_FILE"; then
        sed -i 's/Return<void>/ndk::ScopedAStatus/g' "$CPP_FILE"
        sed -i 's/Return<bool>/ndk::ScopedAStatus/g' "$CPP_FILE"
        sed -i 's/Return<StatusCode>/ndk::ScopedAStatus/g' "$CPP_FILE"
        ok "Fixed Return<> → ScopedAStatus"
        ((CHANGED++))
    fi

    if grep -q "hidl_vec" "$CPP_FILE"; then
        sed -i 's/hidl_vec</std::vector</g' "$CPP_FILE"
        ok "Fixed hidl_vec → std::vector"
        ((CHANGED++))
    fi

    if grep -q "return Void()" "$CPP_FILE"; then
        sed -i 's/return Void();/return ndk::ScopedAStatus::ok();/g' "$CPP_FILE"
        ok "Fixed Void() → ScopedAStatus::ok()"
        ((CHANGED++))
    fi

    # Remove HIDL factory function
    if grep -q "HIDL_FETCH_" "$CPP_FILE"; then
        sed -i '/HIDL_FETCH_/d' "$CPP_FILE"
        ok "Removed HIDL_FETCH_ factory"
        ((CHANGED++))
    fi

    # Remove _hidl_cb callback pattern
    if grep -q "_hidl_cb" "$CPP_FILE"; then
        sed -i 's/_hidl_cb(.*);/\/\/ callback removed — use AIDL return/g' "$CPP_FILE"
        ok "Replaced _hidl_cb callbacks"
        ((CHANGED++))
    fi

    if [ $CHANGED -gt 0 ]; then
        ((FIXES++))
        ok "Applied $CHANGED HIDL→AIDL fixes to $(basename "$CPP_FILE")"
    else
        info "No HIDL patterns found in $(basename "$CPP_FILE")"
    fi
else
    warn "No VehicleHalService*.cpp found"
    ((WARNINGS++))
fi


# ═══════════════════════════════════════════════════════════════
# Summary
# ═══════════════════════════════════════════════════════════════
echo ""
echo "═══════════════════════════════════════════════════════════"
echo " Integration Complete"
echo "═══════════════════════════════════════════════════════════"
echo -e "  Fixes applied:  ${GREEN}${FIXES}${NC}"
echo -e "  Warnings:       ${YELLOW}${WARNINGS}${NC}"
echo ""
echo " Files placed:"
echo "  AIDL:"
ls -1 "$AIDL_DIR"/VehicleProperty{Adas,Vss}*.aidl 2>/dev/null | while read f; do echo "    $(basename "$f")"; done
echo "  C++ impl ($IMPL_DIR):"
ls -1 "$IMPL_DIR"/*.cpp "$IMPL_DIR"/Android.bp* 2>/dev/null | while read f; do echo "    $(basename "$f")"; done
echo "  SELinux ($SEPOLICY_DIR):"
ls -1 "$SEPOLICY_DIR"/vehicle_hal_*.te "$SEPOLICY_DIR"/file_contexts 2>/dev/null | while read f; do echo "    $(basename "$f")"; done
echo ""
echo " Build commands:"
echo "   cd $AOSP_ROOT"
echo "   source build/envsetup.sh"
echo "   lunch aosp_cf_x86_64_auto-trunk_staging-userdebug"
echo ""
echo "   # Update AIDL API (required after adding new .aidl file):"
echo "   m android.hardware.automotive.vehicle-update-api"
echo ""
echo "   # Module build (fast test):"
echo "   mmm hardware/interfaces/automotive/vehicle/aidl/impl"
echo ""
echo "   # Full image build:"
echo "   m -j\$(nproc)"
echo "═══════════════════════════════════════════════════════════"
