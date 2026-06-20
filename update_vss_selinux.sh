#!/bin/bash
# update_vss_selinux.sh
# Run this in AOSP root

AOSP_ROOT=$(pwd)
SEPOL="$AOSP_ROOT/system/sepolicy/vendor"

echo "Updating SELinux for VSS VHAL..."

# 1. Create hal_vehicle_vss.te
cat > "$SEPOL/hal_vehicle_vss.te" << 'EOF'
type hal_vehicle_vss, domain;
type hal_vehicle_vss_exec, exec_type, vendor_file_type, file_type;

init_daemon_domain(hal_vehicle_vss)
hal_server_domain(hal_vehicle_vss, hal_vehicle)

allow hal_vehicle_vss self:process { fork sigchld };
allow hal_vehicle_vss hal_vehicle_default:process signal;
allow hal_vehicle_vss vendor_configs_file:dir search;
allow hal_vehicle_vss vendor_configs_file:file { read getattr open };
EOF
echo "Created hal_vehicle_vss.te"

# 2. Add to file_contexts
echo '/vendor/bin/hw/android\.hardware\.automotive\.vehicle@V3-vss-service u:object_r:hal_vehicle_vss_exec:s0' >> "$SEPOL/file_contexts"
echo "Added to file_contexts"

# 3. Add to service_contexts (if exists)
if [ -f "$SEPOL/service_contexts" ]; then
    echo 'android.hardware.automotive.vehicle.IVehicle/default u:object_r:hal_vehicle_vss_service:s0' >> "$SEPOL/service_contexts"
    echo "Added to service_contexts"
fi

# 4. Make sure .rc is correct (in pipeline output)
echo "Remember to have correct .rc with class early_hal"

echo ""
echo "Done. Now rebuild:"
echo "mmm hardware/interfaces/automotive/vehicle/aidl/impl/vss"
echo "m vendorimage"