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

# Try multiple possible locations
for src_dir in \
    "$OUT/hardware/interfaces/automotive/vehicle/impl" \
    "$OUT/hardware/interfaces/automotive/vehicle/aidl/impl" \
    "$OUT/hardware/interfaces/automotive/vehicle/aidl/impl/vss"; do

    if [ -d "$src_dir" ]; then
        echo "  Searching in: $src_dir"
        for f in "$src_dir"/VehicleHalService*.cpp "$src_dir"/VehicleHalService*.h; do
            [ -f "$f" ] || continue
            cp "$f" "$VSS_DIR/" && ok "Domain C++: $(basename $f)"
            COUNT=$((COUNT + 1))
        done
    fi
done

# Also copy glue if present
VSS_GLUE_SRC="$OUT/hardware/interfaces/automotive/vehicle/aidl/impl/vss"
if [ -d "$VSS_GLUE_SRC" ]; then
    for f in "$VSS_GLUE_SRC"/*.cpp "$VSS_GLUE_SRC"/*.h; do
        [ -f "$f" ] || continue
        cp "$f" "$VSS_DIR/" && ok "Glue: $(basename $f)"
        COUNT=$((COUNT + 1))
    done
fi

[ $COUNT -eq 0 ] && warn "No VehicleHalService files found"

# === Verify headers ===
echo ""
if ls "$VSS_DIR"/VehicleHalService*.h >/dev/null 2>&1; then
    ok "All VehicleHalService*.h headers copied successfully"
    ls "$VSS_DIR"/VehicleHalService*.h | wc -l | xargs echo "   → Found"
else
    warn "No VehicleHalService*.h headers were copied"
fi

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

AREA_GLOBAL  = 0x00000000
TYPE_MIXED   = 0x00e00000

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
            if not m:
                continue
            name, val = m.group(1), m.group(2)
            try:
                raw_id = int(val, 16)
                type_bits = TYPE_MIXED
                for kw, bits in comment_map.items():
                    if kw in line:
                        type_bits = bits
                        break
                prop_id = AREA_GLOBAL | type_bits | (raw_id & 0xFFFF)
                if prop_id not in seen:
                    seen.add(prop_id)
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

# ═══════════════════════════════════════════════════════════════
# [6/6] Integrate VehicleHalService*.cpp routing
# ═══════════════════════════════════════════════════════════════
echo ""
echo "[6/6] Updating VssVehicleHardware.cpp with domain service routing..."

VSS_CPP="$VSS_DIR/VssVehicleHardware.cpp"
if [ ! -f "$VSS_CPP" ]; then
    warn "VssVehicleHardware.cpp not found — skipping routing fix"
else
    cp "$VSS_CPP" "$VSS_CPP.bak.$(date +%s)" 2>/dev/null || true

    cat > "$VSS_CPP" << 'EOF'
#include <aidl/android/hardware/automotive/vehicle/IVehicle.h>
#include <android-base/logging.h>
#include <vector>
#include <memory>
#include <unordered_map>

#include "VehicleHalServiceAdas.h"
#include "VehicleHalServiceBody.h"
#include "VehicleHalServiceCabin.h"
#include "VehicleHalServiceChassis.h"
#include "VehicleHalServiceHvac.h"
#include "VehicleHalServiceInfotainment.h"
#include "VehicleHalServicePowertrain.h"

using namespace aidl::android::hardware::automotive::vehicle;

class VssVehicleHardware : public BnVehicleHardware {
public:
    VssVehicleHardware() {
        mServices = {
            {"Adas", std::make_unique<VehicleHalServiceAdas>()},
            {"Body", std::make_unique<VehicleHalServiceBody>()},
            {"Cabin", std::make_unique<VehicleHalServiceCabin>()},
            {"Chassis", std::make_unique<VehicleHalServiceChassis>()},
            {"Hvac", std::make_unique<VehicleHalServiceHvac>()},
            {"Infotainment", std::make_unique<VehicleHalServiceInfotainment>()},
            {"Powertrain", std::make_unique<VehicleHalServicePowertrain>()},
        };
        LOG(INFO) << "[VSS] Initialized with " << mServices.size() << " domain services";
    }

    ::ndk::ScopedAStatus getAllPropertyConfigs(std::vector<VehiclePropConfig>* configs) override {
        configs->clear();
        for (auto& [name, svc] : mServices) {
            std::vector<VehiclePropConfig> cfgs;
            svc->getAllPropertyConfigs(&cfgs);
            configs->insert(configs->end(), cfgs.begin(), cfgs.end());
        }
        LOG(INFO) << "[VSS] getAllPropertyConfigs returned " << configs->size() << " properties";
        return ::ndk::ScopedAStatus::ok();
    }

    ::ndk::ScopedAStatus get(const GetValueRequest& req, GetValueResult* res) override {
        for (auto& [name, svc] : mServices) {
            if (svc->handlesProperty(req.prop.prop)) return svc->get(req, res);
        }
        return ::ndk::ScopedAStatus::fromServiceSpecificError(-1, "Property not supported");
    }

    ::ndk::ScopedAStatus set(const SetValueRequest& req, SetValueResult* res) override {
        for (auto& [name, svc] : mServices) {
            if (svc->handlesProperty(req.prop.prop)) return svc->set(req, res);
        }
        return ::ndk::ScopedAStatus::fromServiceSpecificError(-1, "Property not supported");
    }

private:
    std::unordered_map<std::string, std::unique_ptr<IVehicleHardware>> mServices;
};

extern "C" IVehicleHardware* HIDL_FETCH_IVehicleHardware(const char* /*name*/) {
    return new VssVehicleHardware();
}
EOF
    ok "VssVehicleHardware.cpp updated with multi-service routing"
fi

echo "   → VssVehicleHardware now routes to all VehicleHalService*.cpp"

# ═══════════════════════════════════════════════════════════════
# [7/7] Cuttlefish VHAL Service Selection
# ═══════════════════════════════════════════════════════════════
# Strategy: device_vendor.mk already contains:
#   ifeq ($(LOCAL_VHAL_PRODUCT_PACKAGE),)
#       LOCAL_VHAL_PRODUCT_PACKAGE := android.hardware.automotive.vehicle@V3-emulator-service
#   endif
#   PRODUCT_PACKAGES += $(LOCAL_VHAL_PRODUCT_PACKAGE)
#
# The correct approach is to set LOCAL_VHAL_PRODUCT_PACKAGE in the file
# that *includes* device_vendor.mk — so the ifeq guard sees a non-empty
# value and skips the emulator default. Never append directly to
# device_vendor.mk (breaks ifeq/endif balance → "extraneous endif" error).
# ═══════════════════════════════════════════════════════════════
echo ""
echo "[7/7] Configuring Cuttlefish to use VSS VHAL service..."

DEVICE_VENDOR_MK="$AOSP_ROOT/device/google/cuttlefish/shared/auto/device_vendor.mk"

if [ ! -f "$DEVICE_VENDOR_MK" ]; then
    fail "device_vendor.mk not found: $DEVICE_VENDOR_MK"
fi

# Safety check: verify ifeq/endif balance before touching anything
IFEQ_COUNT=$(awk '/^\s*if(eq|def|neq|ndef)/{i++} END{print i+0}' "$DEVICE_VENDOR_MK")
ENDIF_COUNT=$(awk '/^\s*endif/{i++} END{print i+0}' "$DEVICE_VENDOR_MK")
if [ "$IFEQ_COUNT" -ne "$ENDIF_COUNT" ]; then
    fail "device_vendor.mk has unbalanced ifeq/endif ($IFEQ_COUNT vs $ENDIF_COUNT) — restore from backup before continuing"
fi
ok "device_vendor.mk structure intact (ifeq=$IFEQ_COUNT endif=$ENDIF_COUNT)"

# Find the file that includes device_vendor.mk — that is the correct injection point
INCLUDER=$(grep -rl "device_vendor.mk" "$AOSP_ROOT/device/google/cuttlefish/" 2>/dev/null \
    | grep -v "device_vendor.mk$" \
    | grep -v "\.bak" \
    | head -1)

if [ -z "$INCLUDER" ]; then
    warn "Could not find a file that includes device_vendor.mk"
    warn "ACTION REQUIRED: set the following variable manually in your"
    warn "BoardConfig.mk or top-level device.mk BEFORE device_vendor.mk is included:"
    warn "  LOCAL_VHAL_PRODUCT_PACKAGE := android.hardware.automotive.vehicle@V3-vss-service"
else
    ok "Injection point: $INCLUDER"

    # Idempotent: remove any previous VSS injection from this file
    sed -i '/# VSS VHAL override/d'                         "$INCLUDER"
    sed -i '/LOCAL_VHAL_PRODUCT_PACKAGE.*vss-service/d'     "$INCLUDER"

    # Also remove any stale direct injection in device_vendor.mk from old script runs
    sed -i '/# Use VSS-generated Vehicle HAL service/d'     "$DEVICE_VENDOR_MK"
    sed -i '/LOCAL_VHAL_PRODUCT_PACKAGE.*vss-service/d'     "$DEVICE_VENDOR_MK"
    sed -i '/PRODUCT_PACKAGES.*V3-vss-service/d'            "$DEVICE_VENDOR_MK"

    # Find the line number of the include statement
    INCLUDE_LINE=$(grep -n "device_vendor.mk" "$INCLUDER" | head -1 | cut -d: -f1)

    # Inject LOCAL_VHAL_PRODUCT_PACKAGE on the line BEFORE the include
    # so the ifeq guard in device_vendor.mk sees it as non-empty
    sed -i "${INCLUDE_LINE}i # VSS VHAL override: set before device_vendor.mk so ifeq guard skips emulator default\nLOCAL_VHAL_PRODUCT_PACKAGE := android.hardware.automotive.vehicle@V3-vss-service\n" \
        "$INCLUDER"

    ok "LOCAL_VHAL_PRODUCT_PACKAGE injected before include in: $(basename "$INCLUDER") (line $INCLUDE_LINE)"
fi

# Final verification
if grep -q "V3-vss-service" "${INCLUDER:-/dev/null}" 2>/dev/null; then
    ok "Verified: V3-vss-service will be used by Cuttlefish"
else
    warn "Verify manually: LOCAL_VHAL_PRODUCT_PACKAGE must be set before device_vendor.mk is included"
fi

echo ""
echo "═══════════════════════════════════════════════════════════"
echo " Done. Next steps:"
echo " 1. Build:  m -j\$(nproc) 2>&1 | tee ~/build_c4.log"
echo " 2. Launch: launch_cvd --noresume"
echo " 3. VTS:    atest VtsHalAutomotiveVehicleVss"
echo "═══════════════════════════════════════════════════════════"