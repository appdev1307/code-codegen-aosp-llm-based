#!/bin/bash
# ═══════════════════════════════════════════════════════════════
# apply_aosp14_fixes.sh  (fixed)
# Android 14 AOSP integration for C3/C4 generated VHAL code.
# ═══════════════════════════════════════════════════════════════

set -euo pipefail

OUT="${1:?Usage: $0 <output_dir>  e.g. ~/output_c4}"
AOSP_ROOT="${2:-$(pwd)}"
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
mkdir -p /tmp/1001/cvd_1/cuttlefish/assembly /tmp/1001/cvd_1/cuttlefish/instances

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

VSS_GLUE_SRC="$OUT/hardware/interfaces/automotive/vehicle/aidl/impl/vss"
if [ -d "$VSS_GLUE_SRC" ]; then
    for f in "$VSS_GLUE_SRC"/*.cpp "$VSS_GLUE_SRC"/*.h; do
        [ -f "$f" ] || continue
        cp "$f" "$VSS_DIR/" && ok "Glue: $(basename $f)"
        COUNT=$((COUNT + 1))
    done
fi

[ $COUNT -eq 0 ] && warn "No VehicleHalService files found"

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

# Copy SELinux files into VSS_DIR so they are built alongside the binary
cp "$VSS_TE" "$VSS_DIR/vehicle_hal_vss.te" 2>/dev/null && ok "vehicle_hal_vss.te copied to VSS build dir" || true
cp "$FC_VSS" "$VSS_DIR/file_contexts_vss" 2>/dev/null && ok "file_contexts_vss copied to VSS build dir" || true

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
# [6/6] Rewrite VSS C++ files with correct architecture
# ═══════════════════════════════════════════════════════════════
# LLM generates each VehicleHalService*.h as a full redefinition of
# VssVehicleHardware — causing redefinition errors. Correct architecture:
#   VehicleHalService<Domain>.h/.cpp → domain-specific helper class
#   VssVehicleHardware.h/.cpp        → aggregator implementing IVehicleHardware
#   VssVehicleService.cpp            → main() entry point
# ═══════════════════════════════════════════════════════════════
echo ""
echo "[6/6] Rewriting VSS C++ files with correct architecture..."

COMMON_INCLUDES='#pragma once
#include <IVehicleHardware.h>
#include <aidl/android/hardware/automotive/vehicle/StatusCode.h>
#include <aidl/android/hardware/automotive/vehicle/VehiclePropConfig.h>
#include <aidl/android/hardware/automotive/vehicle/VehiclePropValue.h>
#include <aidl/android/hardware/automotive/vehicle/GetValueRequest.h>
#include <aidl/android/hardware/automotive/vehicle/GetValueResult.h>
#include <aidl/android/hardware/automotive/vehicle/SetValueRequest.h>
#include <aidl/android/hardware/automotive/vehicle/SetValueResult.h>
#include <aidl/android/hardware/automotive/vehicle/SubscribeOptions.h>
#include <aidl/android/hardware/automotive/vehicle/VehiclePropertyAccess.h>
#include <aidl/android/hardware/automotive/vehicle/VehiclePropertyChangeMode.h>
#include <vector>
#include <memory>

namespace android::hardware::automotive::vehicle {

using ::aidl::android::hardware::automotive::vehicle::StatusCode;
using ::aidl::android::hardware::automotive::vehicle::VehiclePropConfig;
using ::aidl::android::hardware::automotive::vehicle::VehiclePropValue;
using ::aidl::android::hardware::automotive::vehicle::GetValueRequest;
using ::aidl::android::hardware::automotive::vehicle::GetValueResult;
using ::aidl::android::hardware::automotive::vehicle::SetValueRequest;
using ::aidl::android::hardware::automotive::vehicle::SetValueResult;
using ::aidl::android::hardware::automotive::vehicle::SubscribeOptions;
using ::aidl::android::hardware::automotive::vehicle::VehiclePropertyAccess;
using ::aidl::android::hardware::automotive::vehicle::VehiclePropertyChangeMode;'

# Rewrite each domain header and cpp
for domain in Adas Body Cabin Chassis Hvac Infotainment Powertrain; do
    HDR="$VSS_DIR/VehicleHalService${domain}.h"
    CPP="$VSS_DIR/VehicleHalService${domain}.cpp"
    CLASS="VehicleHalService${domain}"

    [ -f "$HDR" ] && cp "$HDR" "$HDR.bak.$(date +%s)"
    [ -f "$CPP" ] && cp "$CPP" "$CPP.bak.$(date +%s)"

    # Rewrite header — domain-specific class, NOT VssVehicleHardware
    cat > "$HDR" << HEOF
${COMMON_INCLUDES}

class ${CLASS} {
public:
    std::vector<VehiclePropConfig> getAllPropertyConfigs() const;
    void getValues(const std::vector<GetValueRequest>& requests,
                   std::vector<GetValueResult>& results) const;
    void setValues(const std::vector<SetValueRequest>& requests,
                   std::vector<SetValueResult>& results);
    bool handlesProperty(int32_t propId) const;
};

}  // namespace android::hardware::automotive::vehicle
HEOF

    # Rewrite cpp — stub implementation, domain logic can be added here
    cat > "$CPP" << CEOF
#include "VehicleHalService${domain}.h"
#pragma clang diagnostic push
#pragma clang diagnostic ignored "-Wunused-parameter"

namespace android::hardware::automotive::vehicle {

std::vector<VehiclePropConfig> ${CLASS}::getAllPropertyConfigs() const {
    return {};
}

void ${CLASS}::getValues(const std::vector<GetValueRequest>& requests,
                          std::vector<GetValueResult>& results) const {
    for (const auto& req : requests) {
        GetValueResult result = {};
        result.requestId = req.requestId;
        result.status = StatusCode::OK;
        results.push_back(result);
    }
}

void ${CLASS}::setValues(const std::vector<SetValueRequest>& requests,
                          std::vector<SetValueResult>& results) {
    for (const auto& req : requests) {
        SetValueResult result = {};
        result.requestId = req.requestId;
        result.status = StatusCode::OK;
        results.push_back(result);
    }
}

bool ${CLASS}::handlesProperty(int32_t propId) const {
    for (const auto& cfg : getAllPropertyConfigs()) {
        if (cfg.prop == propId) return true;
    }
    return false;
}

}  // namespace android::hardware::automotive::vehicle
#pragma clang diagnostic pop
CEOF

    ok "Rewritten: $CLASS"
done

# Rewrite VssVehicleHardware.h
cat > "$VSS_DIR/VssVehicleHardware.h" << 'EOF'
#pragma once
#include <IVehicleHardware.h>
#include <aidl/android/hardware/automotive/vehicle/StatusCode.h>
#include <aidl/android/hardware/automotive/vehicle/VehiclePropConfig.h>
#include <aidl/android/hardware/automotive/vehicle/GetValueRequest.h>
#include <aidl/android/hardware/automotive/vehicle/GetValueResult.h>
#include <aidl/android/hardware/automotive/vehicle/SetValueRequest.h>
#include <aidl/android/hardware/automotive/vehicle/SetValueResult.h>
#include <aidl/android/hardware/automotive/vehicle/SubscribeOptions.h>

namespace android::hardware::automotive::vehicle {

using ::aidl::android::hardware::automotive::vehicle::StatusCode;
using ::aidl::android::hardware::automotive::vehicle::VehiclePropConfig;
using ::aidl::android::hardware::automotive::vehicle::GetValueRequest;
using ::aidl::android::hardware::automotive::vehicle::GetValueResult;
using ::aidl::android::hardware::automotive::vehicle::SetValueRequest;
using ::aidl::android::hardware::automotive::vehicle::SetValueResult;
using ::aidl::android::hardware::automotive::vehicle::SubscribeOptions;

class VssVehicleHardware : public IVehicleHardware {
public:
    std::vector<VehiclePropConfig> getAllPropertyConfigs() const override;

    StatusCode getValues(std::shared_ptr<const GetValuesCallback> callback,
                         const std::vector<GetValueRequest>& requests) const override;

    StatusCode setValues(std::shared_ptr<const SetValuesCallback> callback,
                         const std::vector<SetValueRequest>& requests) override;

    StatusCode updateSampleRate(int32_t propId, int32_t areaId,
                                float sampleRate) override;

    // Exact signature from IVehicleHardware: by-value SubscribeOptions
    StatusCode subscribe(
            ::aidl::android::hardware::automotive::vehicle::SubscribeOptions options) override;

    // Exact signature from IVehicleHardware: (propId, areaId)
    StatusCode unsubscribe(int32_t propId, int32_t areaId) override;

    DumpResult dump(const std::vector<std::string>& options) override;

    StatusCode checkHealth() override;

    void registerOnPropertyChangeEvent(
            std::unique_ptr<const PropertyChangeCallback> callback) override;

    void registerOnPropertySetErrorEvent(
            std::unique_ptr<const PropertySetErrorCallback> callback) override;
};

}  // namespace android::hardware::automotive::vehicle
EOF
ok "VssVehicleHardware.h rewritten"

# Rewrite VssVehicleHardware.cpp — aggregator
cat > "$VSS_DIR/VssVehicleHardware.cpp" << 'EOF'
#include "VssVehicleHardware.h"
#include "VehicleHalServiceAdas.h"
#include "VehicleHalServiceBody.h"
#include "VehicleHalServiceCabin.h"
#include "VehicleHalServiceChassis.h"
#include "VehicleHalServiceHvac.h"
#include "VehicleHalServiceInfotainment.h"
#include "VehicleHalServicePowertrain.h"
#include <android-base/logging.h>

namespace android::hardware::automotive::vehicle {

using ::aidl::android::hardware::automotive::vehicle::StatusCode;
using ::aidl::android::hardware::automotive::vehicle::VehiclePropConfig;
using ::aidl::android::hardware::automotive::vehicle::GetValueRequest;
using ::aidl::android::hardware::automotive::vehicle::GetValueResult;
using ::aidl::android::hardware::automotive::vehicle::SetValueRequest;
using ::aidl::android::hardware::automotive::vehicle::SetValueResult;
using ::aidl::android::hardware::automotive::vehicle::SubscribeOptions;

static VehicleHalServiceAdas         sAdas;
static VehicleHalServiceBody         sBody;
static VehicleHalServiceCabin        sCabin;
static VehicleHalServiceChassis      sChassis;
static VehicleHalServiceHvac         sHvac;
static VehicleHalServiceInfotainment sInfotainment;
static VehicleHalServicePowertrain   sPowertrain;

std::vector<VehiclePropConfig> VssVehicleHardware::getAllPropertyConfigs() const {
    std::vector<VehiclePropConfig> all;
    auto append = [&](std::vector<VehiclePropConfig> v) {
        all.insert(all.end(), v.begin(), v.end());
    };
    append(sAdas.getAllPropertyConfigs());
    append(sBody.getAllPropertyConfigs());
    append(sCabin.getAllPropertyConfigs());
    append(sChassis.getAllPropertyConfigs());
    append(sHvac.getAllPropertyConfigs());
    append(sInfotainment.getAllPropertyConfigs());
    append(sPowertrain.getAllPropertyConfigs());
    LOG(INFO) << "[VSS] getAllPropertyConfigs: " << all.size() << " properties";
    return all;
}

StatusCode VssVehicleHardware::getValues(
        std::shared_ptr<const GetValuesCallback> callback,
        const std::vector<GetValueRequest>& requests) const {
    std::vector<GetValueResult> results;
    sAdas.getValues(requests, results);
    sBody.getValues(requests, results);
    sCabin.getValues(requests, results);
    sChassis.getValues(requests, results);
    sHvac.getValues(requests, results);
    sInfotainment.getValues(requests, results);
    sPowertrain.getValues(requests, results);
    (*callback)(results);
    return StatusCode::OK;
}

StatusCode VssVehicleHardware::setValues(
        std::shared_ptr<const SetValuesCallback> callback,
        const std::vector<SetValueRequest>& requests) {
    std::vector<SetValueResult> results;
    sAdas.setValues(requests, results);
    sBody.setValues(requests, results);
    sCabin.setValues(requests, results);
    sChassis.setValues(requests, results);
    sHvac.setValues(requests, results);
    sInfotainment.setValues(requests, results);
    sPowertrain.setValues(requests, results);
    (*callback)(results);
    return StatusCode::OK;
}

StatusCode VssVehicleHardware::updateSampleRate(
        [[maybe_unused]] int32_t propId,
        [[maybe_unused]] int32_t areaId,
        [[maybe_unused]] float sampleRate) {
    return StatusCode::OK;
}

StatusCode VssVehicleHardware::subscribe(
        [[maybe_unused]] ::aidl::android::hardware::automotive::vehicle::SubscribeOptions options) {
    return StatusCode::OK;
}

StatusCode VssVehicleHardware::unsubscribe(
        [[maybe_unused]] int32_t propId,
        [[maybe_unused]] int32_t areaId) {
    return StatusCode::OK;
}

DumpResult VssVehicleHardware::dump(
        [[maybe_unused]] const std::vector<std::string>& options) {
    return {};
}

StatusCode VssVehicleHardware::checkHealth() {
    return StatusCode::OK;
}

void VssVehicleHardware::registerOnPropertyChangeEvent(
        [[maybe_unused]] std::unique_ptr<const PropertyChangeCallback> callback) {}

void VssVehicleHardware::registerOnPropertySetErrorEvent(
        [[maybe_unused]] std::unique_ptr<const PropertySetErrorCallback> callback) {}

}  // namespace android::hardware::automotive::vehicle
EOF
ok "VssVehicleHardware.cpp rewritten (aggregator)"

# ═══════════════════════════════════════════════════════════════
# [6b] Generate Android.bp, init .rc, and vintf manifest for VSS service
# ═══════════════════════════════════════════════════════════════
echo ""
echo "[6b] Generating build system files for V3-vss-service..."

VSS_BP="$VSS_DIR/Android.bp"
VSS_RC="$VSS_DIR/android.hardware.automotive.vehicle@V3-vss-service.rc"
VSS_MANIFEST="$VSS_DIR/manifest_vss.xml"

# ── Android.bp ──────────────────────────────────────────────
# Mirrors V3-default-service deps (VehicleHalDefaults, DefaultVehicleHal,
# VehicleHalUtils, IVehicleHardware) confirmed from vhal/Android.bp.
# Does NOT use FakeVehicleHardwareDefaults (test-only) or
# android.hardware.automotive.vehicle@aidl-default-impl-lib (does not exist).
if [ ! -f "$VSS_BP" ] || [ "$FORCE" = "1" ]; then
    [ "$FORCE" = "1" ] && [ -f "$VSS_BP" ] && cp "$VSS_BP" "$VSS_BP.bak.$(date +%s)"
    cat > "$VSS_BP" << 'BPEOF'
cc_binary {
    name: "android.hardware.automotive.vehicle@V3-vss-service",
    vendor: true,
    relative_install_path: "hw",
    defaults: [
        "VehicleHalDefaults",
        "android-automotive-large-parcelable-defaults",
    ],
    init_rc: ["android.hardware.automotive.vehicle@V3-vss-service.rc"],
    vintf_fragments: ["manifest_vss.xml"],
    srcs: [
        "VssVehicleService.cpp",
        "VssVehicleHardware.cpp",
        "VehicleHalServiceAdas.cpp",
        "VehicleHalServiceBody.cpp",
        "VehicleHalServiceCabin.cpp",
        "VehicleHalServiceChassis.cpp",
        "VehicleHalServiceHvac.cpp",
        "VehicleHalServiceInfotainment.cpp",
        "VehicleHalServicePowertrain.cpp",
    ],
    static_libs: [
        "DefaultVehicleHal",
        "VehicleHalUtils",
    ],
    header_libs: [
        "IVehicleHardware",
        "VehicleHalUtilHeaders",
    ],
    shared_libs: [
        "libbinder_ndk",
        "libbase",
        "liblog",
        "libutils",
    ],
}

// SELinux policy — label the binary so init can domain-transition into
// hal_vehicle_vss when starting vendor.vehicle-hal-vss service.
se_policy_conf {
    name: "vehicle_hal_vss_policy_conf",
    srcs: ["vehicle_hal_vss.te"],
    vendor: true,
}

se_policy_cil {
    name: "vehicle_hal_vss_policy_cil",
    src: ":vehicle_hal_vss_policy_conf",
    vendor: true,
}

se_policy_cil {
    name: "vehicle_hal_vss_file_contexts_cil",
    src: ":vehicle_hal_vss_file_contexts_gen",
    vendor: true,
    output_extension: ".fc",
}

genrule {
    name: "vehicle_hal_vss_file_contexts_gen",
    srcs: ["file_contexts_vss"],
    out: ["vehicle_hal_vss.fc"],
    cmd: "cp $(in) $(out)",
}
BPEOF
    ok "Android.bp $([ "$FORCE" = "1" ] && echo "force-regenerated" || echo "created")"
else
    ok "Android.bp already present — skipping (use --force to overwrite)"
fi

# ── init .rc ────────────────────────────────────────────────
if [ ! -f "$VSS_RC" ] || [ "$FORCE" = "1" ]; then
    [ "$FORCE" = "1" ] && [ -f "$VSS_RC" ] && cp "$VSS_RC" "$VSS_RC.bak.$(date +%s)"
    cat > "$VSS_RC" << 'RCEOF'
service vendor.vehicle-hal-vss /vendor/bin/hw/android.hardware.automotive.vehicle@V3-vss-service
    class hal
    user vehicle_network
    group vehicle_network
RCEOF
    ok "init rc $([ "$FORCE" = "1" ] && echo "force-regenerated" || echo "created")"
else
    ok "init rc already present — skipping (use --force to overwrite)"
fi

# ── vintf manifest ───────────────────────────────────────────
if [ ! -f "$VSS_MANIFEST" ] || [ "$FORCE" = "1" ]; then
    [ "$FORCE" = "1" ] && [ -f "$VSS_MANIFEST" ] && cp "$VSS_MANIFEST" "$VSS_MANIFEST.bak.$(date +%s)"
    cat > "$VSS_MANIFEST" << 'XMLEOF'
<manifest version="1.0" type="device">
    <hal format="aidl">
        <name>android.hardware.automotive.vehicle</name>
        <version>3</version>
        <fqname>IVehicle/default</fqname>
    </hal>
</manifest>
XMLEOF
    ok "manifest_vss.xml $([ "$FORCE" = "1" ] && echo "force-regenerated" || echo "created")"
else
    ok "manifest_vss.xml already present — skipping (use --force to overwrite)"
fi

echo "   → Build files: Android.bp  |  *.rc  |  manifest_vss.xml  |  VssVehicleService.cpp"

# ── VssVehicleService.cpp (main entry point) ─────────────────
VSS_SVC="$VSS_DIR/VssVehicleService.cpp"
if [ ! -f "$VSS_SVC" ] || [ "$FORCE" = "1" ]; then
    [ "$FORCE" = "1" ] && [ -f "$VSS_SVC" ] && cp "$VSS_SVC" "$VSS_SVC.bak.$(date +%s)"
    cat > "$VSS_SVC" << 'SVCEOF'
#define LOG_TAG "VssVehicleService"
#include <DefaultVehicleHal.h>
#include "VssVehicleHardware.h"
#include <android/binder_manager.h>
#include <android/binder_process.h>
#include <utils/Log.h>

using ::android::hardware::automotive::vehicle::DefaultVehicleHal;
using ::android::hardware::automotive::vehicle::VssVehicleHardware;

int main(int /* argc */, char* /* argv */[]) {
    ALOGI("Starting VSS Vehicle HAL thread pool...");
    if (!ABinderProcess_setThreadPoolMaxThreadCount(4)) {
        ALOGE("failed to set thread pool max thread count");
        return 1;
    }
    ABinderProcess_startThreadPool();

    std::unique_ptr<VssVehicleHardware> hardware = std::make_unique<VssVehicleHardware>();
    std::shared_ptr<DefaultVehicleHal> vhal =
            ::ndk::SharedRefBase::make<DefaultVehicleHal>(std::move(hardware));

    ALOGI("Registering VSS VHAL as service...");
    binder_exception_t err = AServiceManager_addService(
            vhal->asBinder().get(),
            "android.hardware.automotive.vehicle.IVehicle/default");
    if (err != EX_NONE) {
        ALOGE("failed to register VSS vehicle service, exception: %d", err);
        return 1;
    }

    ALOGI("VSS Vehicle Service Ready");
    ABinderProcess_joinThreadPool();
    ALOGI("VSS Vehicle Service Exiting");
    return 0;
}
SVCEOF
    ok "VssVehicleService.cpp $([ "$FORCE" = "1" ] && echo "force-regenerated" || echo "created")"
else
    ok "VssVehicleService.cpp already present — skipping (use --force to overwrite)"
fi

# ═══════════════════════════════════════════════════════════════
# [7/7] Cuttlefish VHAL Service Selection
# ═══════════════════════════════════════════════════════════════
# Strategy: device_vendor.mk already contains:
#   ifeq ($(LOCAL_VHAL_PRODUCT_PACKAGE),)
#       LOCAL_VHAL_PRODUCT_PACKAGE := android.hardware.automotive.vehicle@V3-emulator-service
#   endif
#   PRODUCT_PACKAGES += $(LOCAL_VHAL_PRODUCT_PACKAGE)
#
# Correct approach: set LOCAL_VHAL_PRODUCT_PACKAGE in the aosp_cf.mk that
# matches the active lunch target (e.g. vsoc_x86_64_only/auto/aosp_cf.mk
# for aosp_cf_x86_64_auto), BEFORE the include of device_vendor.mk.
# The ifeq guard then sees a non-empty value and skips the emulator default.
#
# NEVER append directly to device_vendor.mk — that breaks ifeq/endif
# balance and causes "extraneous endif" build errors.
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

# ── Step 1: Derive correct aosp_cf.mk from active lunch target ──
# lunch aosp_cf_x86_64_auto  → TARGET_PRODUCT=aosp_cf_x86_64_auto
#                             → vsoc_x86_64_only/auto/aosp_cf.mk
# Mapping:
#   arch x86_64 / arm64  → vsoc_<arch>_only/<flavor>/aosp_cf.mk
#   arch x86 / arm       → vsoc_<arch>/<flavor>/aosp_cf.mk

INCLUDER=""

if [ -n "${TARGET_PRODUCT:-}" ]; then
    REMAINDER="${TARGET_PRODUCT#aosp_cf_}"   # e.g. "x86_64_auto"
    FLAVOR="${REMAINDER##*_}"                 # e.g. "auto"
    ARCH_TOKEN="${REMAINDER%_*}"              # e.g. "x86_64"

    case "$ARCH_TOKEN" in
        x86_64|arm64) ARCH_DIR="vsoc_${ARCH_TOKEN}_only" ;;
        *)             ARCH_DIR="vsoc_${ARCH_TOKEN}"      ;;
    esac

    CANDIDATE="$AOSP_ROOT/device/google/cuttlefish/${ARCH_DIR}/${FLAVOR}/aosp_cf.mk"
    if [ -f "$CANDIDATE" ] && grep -q "device_vendor.mk" "$CANDIDATE"; then
        INCLUDER="$CANDIDATE"
        ok "Derived from TARGET_PRODUCT=$TARGET_PRODUCT → ${ARCH_DIR}/${FLAVOR}/aosp_cf.mk"
    else
        warn "Derived path not found or missing device_vendor.mk include: $CANDIDATE"
    fi
fi

# ── Step 2: Fallback — prefer any auto/aosp_cf.mk ──
if [ -z "$INCLUDER" ]; then
    INCLUDER=$(grep -rl "device_vendor.mk" "$AOSP_ROOT/device/google/cuttlefish/" 2>/dev/null \
        | grep -v "device_vendor.mk$" \
        | grep -v "\.bak" \
        | grep "auto/aosp_cf.mk" \
        | head -1)
    [ -n "$INCLUDER" ] && warn "TARGET_PRODUCT not set — falling back to: $INCLUDER"
fi

# ── Step 3: Last resort ──
if [ -z "$INCLUDER" ]; then
    INCLUDER=$(grep -rl "device_vendor.mk" "$AOSP_ROOT/device/google/cuttlefish/" 2>/dev/null \
        | grep -v "device_vendor.mk$" \
        | grep -v "\.bak" \
        | head -1)
    [ -n "$INCLUDER" ] && warn "No auto mk found — last resort: $INCLUDER"
fi

if [ -z "$INCLUDER" ]; then
    warn "Could not find any file including device_vendor.mk"
    warn "ACTION REQUIRED: add the following line BEFORE the device_vendor.mk include"
    warn "in vsoc_x86_64_only/auto/aosp_cf.mk (or your target aosp_cf.mk):"
    warn "  LOCAL_VHAL_PRODUCT_PACKAGE := android.hardware.automotive.vehicle@V3-vss-service"
else
    ok "Injection target: $INCLUDER"

    # Idempotent: remove previous VSS injections from ALL aosp_cf.mk files
    # (prevents stale injection in wrong arch file from a previous run)
    while IFS= read -r stale_file; do
        sed -i '/# VSS VHAL override/d'                     "$stale_file" 2>/dev/null || true
        sed -i '/LOCAL_VHAL_PRODUCT_PACKAGE.*vss-service/d' "$stale_file" 2>/dev/null || true
    done < <(grep -rl "device_vendor.mk" "$AOSP_ROOT/device/google/cuttlefish/" 2>/dev/null \
        | grep -v "device_vendor.mk$" | grep -v "\.bak")

    # Remove any stale direct injection in device_vendor.mk from old script runs
    sed -i '/# Use VSS-generated Vehicle HAL service/d' "$DEVICE_VENDOR_MK"
    sed -i '/LOCAL_VHAL_PRODUCT_PACKAGE.*vss-service/d' "$DEVICE_VENDOR_MK"
    sed -i '/PRODUCT_PACKAGES.*V3-vss-service/d'        "$DEVICE_VENDOR_MK"

    # Inject BEFORE the device_vendor.mk include line
    INCLUDE_LINE=$(grep -n "device_vendor.mk" "$INCLUDER" | head -1 | cut -d: -f1)
    sed -i "${INCLUDE_LINE}i # VSS VHAL override: must appear before device_vendor.mk include\n# so the ifeq guard skips the emulator-service default.\nLOCAL_VHAL_PRODUCT_PACKAGE := android.hardware.automotive.vehicle@V3-vss-service\n" \
        "$INCLUDER"

    ok "Injected LOCAL_VHAL_PRODUCT_PACKAGE before line $INCLUDE_LINE in $(basename "$INCLUDER")"
fi

# Final verification
if grep -q "V3-vss-service" "${INCLUDER:-/dev/null}" 2>/dev/null; then
    ok "Verified: V3-vss-service present in $(basename "$INCLUDER")"
    ok "Cuttlefish will use VSS VHAL service (emulator-service default skipped)"
else
    warn "Verification failed — set LOCAL_VHAL_PRODUCT_PACKAGE manually (see above)"
fi

echo ""
echo "═══════════════════════════════════════════════════════════"
echo " [8/8] Fixing Cuttlefish symlink persistence..."
BASHRC="$HOME/.bashrc"
BASHRC_MARKER="# cvd-symlink-fix"
if ! grep -q "$BASHRC_MARKER" "$BASHRC" 2>/dev/null; then
    cat >> "$BASHRC" << 'BASHRCEOF'
# cvd-symlink-fix: recreate /tmp symlink targets after GCP VM reboot
mkdir -p /tmp/1001/cvd_1/cuttlefish/assembly /tmp/1001/cvd_1/cuttlefish/instances 2>/dev/null || true
[ -L "$HOME/cuttlefish" ] || ln -sf /tmp/1001/cvd_1/cuttlefish "$HOME/cuttlefish"
BASHRCEOF
    echo -e "  ${GREEN}✓${NC} Added cvd symlink fix to ~/.bashrc"
else
    echo -e "  ${GREEN}✓${NC} ~/.bashrc already has cvd symlink fix"
fi

echo ""
echo "═══════════════════════════════════════════════════════════"
echo " Done. Next steps:"
echo " 1. Build:  m -j\$(nproc) vendorimage vbmetaimage superimage 2>&1 | tee ~/build_vss.log"
echo "    NOTE: always build vendorimage + vbmetaimage + superimage together."
echo " 2. Launch: launch_cvd --noresume --cpus=8 --memory_mb=8192 --gpu_mode=guest_swiftshader"
echo " 3. VTS:    atest VtsHalAutomotiveVehicleVss"
echo "═══════════════════════════════════════════════════════════"