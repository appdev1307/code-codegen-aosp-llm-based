#!/bin/bash
# ═══════════════════════════════════════════════════════════════
# apply_aosp14_fixes.sh
# ═══════════════════════════════════════════════════════════════
# Automatically applies Android 14 compatibility fixes to
# generated HAL files before AOSP build.
#
# Usage:
#   ./apply_aosp14_fixes.sh <c4_output_dir> [aosp_root]
#
# Example:
#   ./apply_aosp14_fixes.sh ~/output_c4_feedback ~/aosp-14-auto
# ═══════════════════════════════════════════════════════════════

set -euo pipefail

C4_OUT="${1:?Usage: $0 <c4_output_dir> [aosp_root]}"
AOSP_ROOT="${2:-$(pwd)}"

RED='\033[0;31m'
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
NC='\033[0m'

ok()   { echo -e "  ${GREEN}✓${NC} $1"; }
warn() { echo -e "  ${YELLOW}⚠${NC} $1"; }
fail() { echo -e "  ${RED}✗${NC} $1"; exit 1; }

echo "═══════════════════════════════════════════════════════════"
echo " Android 14 AOSP Integration — Automated Fixes"
echo "═══════════════════════════════════════════════════════════"
echo "  C4 output: $C4_OUT"
echo "  AOSP root: $AOSP_ROOT"
echo ""

# ── Verify paths ─────────────────────────────────────────────
[ -d "$C4_OUT" ] || fail "C4 output dir not found: $C4_OUT"
[ -d "$AOSP_ROOT/build" ] || fail "AOSP root invalid (no build/ dir): $AOSP_ROOT"

FIXES=0
WARNINGS=0

# ═══════════════════════════════════════════════════════════════
# Step 1: Copy files to AOSP tree
# ═══════════════════════════════════════════════════════════════
echo "[1/5] Copying generated files to AOSP tree..."

AIDL_DIR="$AOSP_ROOT/hardware/interfaces/automotive/vehicle/aidl/android/hardware/automotive/vehicle"
IMPL_DIR="$AOSP_ROOT/hardware/interfaces/automotive/vehicle/impl"
SEPOLICY_DIR="$AOSP_ROOT/system/sepolicy/vendor"

mkdir -p "$AIDL_DIR" "$IMPL_DIR" "$SEPOLICY_DIR"

# AIDL
AIDL_SRC=$(find "$C4_OUT" -name "*.aidl" -path "*/vehicle/*" | head -1)
if [ -n "$AIDL_SRC" ]; then
    cp "$AIDL_SRC" "$AIDL_DIR/"
    ok "AIDL: $(basename $AIDL_SRC)"
else
    warn "No AIDL file found in $C4_OUT"
    ((WARNINGS++))
fi

# C++
CPP_SRC=$(find "$C4_OUT" -name "*.cpp" -path "*/impl/*" | head -1)
if [ -n "$CPP_SRC" ]; then
    cp "$CPP_SRC" "$IMPL_DIR/"
    ok "C++: $(basename $CPP_SRC)"
else
    warn "No C++ file found in $C4_OUT"
    ((WARNINGS++))
fi

# Android.bp
BP_SRC=$(find "$C4_OUT" -name "Android.bp" -path "*/impl/*" | head -1)
if [ -n "$BP_SRC" ]; then
    cp "$BP_SRC" "$IMPL_DIR/Android.bp.generated"
    ok "Android.bp → Android.bp.generated"
else
    warn "No Android.bp found in $C4_OUT"
    ((WARNINGS++))
fi

# SELinux
TE_SRC=$(find "$C4_OUT" -name "*.te" | head -1)
if [ -n "$TE_SRC" ]; then
    cp "$TE_SRC" "$SEPOLICY_DIR/"
    ok "SELinux: $(basename $TE_SRC)"
else
    warn "No .te file found in $C4_OUT"
    ((WARNINGS++))
fi

# file_contexts
FC_SRC=$(find "$C4_OUT" -name "file_contexts" | head -1)
if [ -n "$FC_SRC" ]; then
    cp "$FC_SRC" "$SEPOLICY_DIR/"
    ok "file_contexts"
fi

# ═══════════════════════════════════════════════════════════════
# Step 2: Fix Android.bp — add vendor: true
# ═══════════════════════════════════════════════════════════════
echo ""
echo "[2/5] Fix Android.bp — adding vendor: true..."

BP_FILE="$IMPL_DIR/Android.bp.generated"
if [ -f "$BP_FILE" ]; then
    if ! grep -q "vendor: true" "$BP_FILE"; then
        # Insert vendor: true after cc_binary { or cc_library_shared {
        sed -i '/cc_binary\s*{/a\    vendor: true,\n    relative_install_path: "hw",' "$BP_FILE"
        if grep -q "vendor: true" "$BP_FILE"; then
            ok "Added vendor: true to Android.bp"
            ((FIXES++))
        else
            # Try alternative: insert after first {
            sed -i '0,/{/a\    vendor: true,\n    relative_install_path: "hw",' "$BP_FILE"
            if grep -q "vendor: true" "$BP_FILE"; then
                ok "Added vendor: true to Android.bp (fallback)"
                ((FIXES++))
            else
                warn "Could not inject vendor: true — manual edit needed"
                ((WARNINGS++))
            fi
        fi
    else
        ok "vendor: true already present"
    fi
else
    warn "Android.bp.generated not found — skipping"
    ((WARNINGS++))
fi

# ═══════════════════════════════════════════════════════════════
# Step 3: Fix SELinux — add type declarations
# ═══════════════════════════════════════════════════════════════
echo ""
echo "[3/5] Fix SELinux — adding type declarations..."

TE_FILE=$(find "$SEPOLICY_DIR" -name "*.te" | head -1)
if [ -n "$TE_FILE" ] && [ -f "$TE_FILE" ]; then
    # Extract domain name from filename (e.g. hal_vehicle_adas from hal_vehicle_adas.te)
    DOMAIN=$(basename "$TE_FILE" .te)

    if ! grep -q "^type ${DOMAIN}," "$TE_FILE"; then
        # Prepend type declarations
        HEADER="# Auto-generated type declarations for Android 14
type ${DOMAIN}, domain;
type ${DOMAIN}_exec, exec_type, vendor_file_type, file_type;

# Allow init to start the service
init_daemon_domain(${DOMAIN})
"
        echo "$HEADER" | cat - "$TE_FILE" > "${TE_FILE}.tmp"
        mv "${TE_FILE}.tmp" "$TE_FILE"
        ok "Added type declarations for ${DOMAIN}"
        ((FIXES++))
    else
        ok "Type declarations already present"
    fi

    # Remove any leading { that would cause syntax error
    if head -5 "$TE_FILE" | grep -q "^{"; then
        sed -i '/^{$/d' "$TE_FILE"
        ok "Removed stray opening brace"
        ((FIXES++))
    fi
else
    warn "No .te file found — skipping"
    ((WARNINGS++))
fi

# ═══════════════════════════════════════════════════════════════
# Step 4: Fix C++ — HIDL → AIDL include paths
# ═══════════════════════════════════════════════════════════════
echo ""
echo "[4/5] Fix C++ — converting HIDL includes to AIDL..."

CPP_FILE=$(find "$IMPL_DIR" -name "*.cpp" | head -1)
if [ -n "$CPP_FILE" ] && [ -f "$CPP_FILE" ]; then
    CHANGED=0

    # Fix HIDL-style includes → AIDL
    if grep -q "android/hardware/automotive/vehicle/2\.0/" "$CPP_FILE"; then
        sed -i 's|android/hardware/automotive/vehicle/2\.0/|aidl/android/hardware/automotive/vehicle/|g' "$CPP_FILE"
        ok "Converted HIDL 2.0 includes to AIDL"
        ((FIXES++))
        ((CHANGED++))
    fi

    # Fix V2_0 namespace references
    if grep -q "V2_0" "$CPP_FILE"; then
        sed -i 's/::V2_0//g' "$CPP_FILE"
        ok "Removed V2_0 namespace qualifiers"
        ((FIXES++))
        ((CHANGED++))
    fi

    # Fix using namespace with V2_0
    if grep -q "namespace.*V2_0" "$CPP_FILE"; then
        sed -i '/namespace.*V2_0/d' "$CPP_FILE"
        ok "Removed V2_0 using namespace declarations"
        ((FIXES++))
        ((CHANGED++))
    fi

    if [ $CHANGED -eq 0 ]; then
        ok "C++ includes already use AIDL paths"
    fi
else
    warn "No C++ file found — skipping"
    ((WARNINGS++))
fi

# ═══════════════════════════════════════════════════════════════
# Step 5: Fix AIDL — package format
# ═══════════════════════════════════════════════════════════════
echo ""
echo "[5/5] Fix AIDL — verifying Android 14 package format..."

AIDL_FILE=$(find "$AIDL_DIR" -name "*.aidl" | head -1)
if [ -n "$AIDL_FILE" ] && [ -f "$AIDL_FILE" ]; then
    # Fix V2_0 in AIDL package declaration
    if grep -q "package.*\.V2_0" "$AIDL_FILE"; then
        sed -i 's/package android\.hardware\.automotive\.vehicle\.V2_0;/package android.hardware.automotive.vehicle;/' "$AIDL_FILE"
        ok "Fixed AIDL package: removed V2_0 suffix"
        ((FIXES++))
    else
        ok "AIDL package format already correct"
    fi

    # Verify @VintfStability annotation for Android 14
    if ! grep -q "@VintfStability" "$AIDL_FILE"; then
        # Add @VintfStability before interface declaration
        sed -i '/^interface /i @VintfStability' "$AIDL_FILE"
        if grep -q "@VintfStability" "$AIDL_FILE"; then
            ok "Added @VintfStability annotation"
            ((FIXES++))
        fi
    else
        ok "@VintfStability already present"
    fi
else
    warn "No AIDL file found — skipping"
    ((WARNINGS++))
fi

# ═══════════════════════════════════════════════════════════════
# Summary
# ═══════════════════════════════════════════════════════════════
echo ""
echo "═══════════════════════════════════════════════════════════"
echo " Summary"
echo "═══════════════════════════════════════════════════════════"
echo -e "  Fixes applied: ${GREEN}${FIXES}${NC}"
echo -e "  Warnings:      ${YELLOW}${WARNINGS}${NC}"
echo ""
echo " Files in AOSP tree:"
echo "  $AIDL_DIR/"
ls -la "$AIDL_DIR"/*.aidl 2>/dev/null | awk '{print "    " $NF}'
echo "  $IMPL_DIR/"
ls -la "$IMPL_DIR"/*.cpp "$IMPL_DIR"/Android.bp* 2>/dev/null | awk '{print "    " $NF}'
echo "  $SEPOLICY_DIR/"
ls -la "$SEPOLICY_DIR"/*.te "$SEPOLICY_DIR"/file_contexts 2>/dev/null | awk '{print "    " $NF}'
echo ""
echo " Next steps:"
echo "   cd $AOSP_ROOT"
echo "   source build/envsetup.sh"
echo "   lunch aosp_cf_x86_64_auto-userdebug"
echo "   mmm hardware/interfaces/automotive/vehicle/impl"
echo "═══════════════════════════════════════════════════════════"
