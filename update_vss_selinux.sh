#!/bin/bash
# update_vss_selinux.sh
# Thêm SELinux policy cho VSS VHAL vào device tree của Cuttlefish (KHÔNG sửa core sepolicy).
# Idempotent: chạy nhiều lần không tạo dòng trùng.
#
# Cách dùng:
#   source build/envsetup.sh && lunch aosp_cf_x86_64_auto-userdebug   # (hoặc target của bạn)
#   ./update_vss_selinux.sh            # build policy + vendorimage rồi relaunch
#   ./update_vss_selinux.sh --no-build # chỉ ghi policy, không build
#   ./update_vss_selinux.sh --permissive   # thêm 'permissive' để gom denial khi debug

set -euo pipefail

# ---- Cấu hình -------------------------------------------------------------
# Đổi VHAL_BINARY cho khớp module name trong Android.bp của bạn.
VHAL_BINARY='android\.hardware\.automotive\.vehicle@V3-vss-service'
DO_BUILD=1
PERMISSIVE=0

for arg in "$@"; do
  case "$arg" in
    --no-build)   DO_BUILD=0 ;;
    --permissive) PERMISSIVE=1 ;;
    *) echo "Unknown arg: $arg"; exit 1 ;;
  esac
done

# ---- Xác định AOSP root ---------------------------------------------------
AOSP_ROOT="${ANDROID_BUILD_TOP:-$(pwd)}"
if [ ! -d "$AOSP_ROOT/build/soong" ]; then
  echo "ERROR: Không thấy AOSP root. Hãy 'source build/envsetup.sh && lunch ...' trước." >&2
  exit 1
fi

# Sepolicy dir của Cuttlefish (device tree, KHÔNG phải system/sepolicy/vendor).
SEPOL="$AOSP_ROOT/device/google/cuttlefish/shared/sepolicy/vendor"
if [ ! -d "$SEPOL" ]; then
  echo "ERROR: Không thấy $SEPOL" >&2
  echo "       Kiểm tra lại path device tree, hoặc dùng dir đã khai trong BOARD_VENDOR_SEPOLICY_DIRS." >&2
  exit 1
fi

TE_FILE="$SEPOL/hal_vehicle_vss.te"
FC_FILE="$SEPOL/file_contexts"

echo "AOSP_ROOT = $AOSP_ROOT"
echo "SEPOL     = $SEPOL"
echo ""

# ---- Helper: append nếu chưa có (idempotent) ------------------------------
append_once() {
  local line="$1" file="$2"
  touch "$file"
  if grep -qF -- "$line" "$file"; then
    echo "  [skip] đã có trong $(basename "$file")"
  else
    echo "$line" >> "$file"
    echo "  [add ] $(basename "$file")"
  fi
}

# ---- 1. hal_vehicle_vss.te ------------------------------------------------
# Ghi đè cả file (file này do mình quản lý nên ghi đè là an toàn & idempotent).
echo "[1/3] Ghi $TE_FILE"
PERMISSIVE_LINE=""
[ "$PERMISSIVE" -eq 1 ] && PERMISSIVE_LINE="permissive hal_vehicle_vss;"

cat > "$TE_FILE" << EOF
type hal_vehicle_vss, domain;
type hal_vehicle_vss_exec, exec_type, vendor_file_type, file_type;

init_daemon_domain(hal_vehicle_vss)
hal_server_domain(hal_vehicle_vss, hal_vehicle)

allow hal_vehicle_vss self:process { fork sigchld };
allow hal_vehicle_vss hal_vehicle_default:process signal;

# VSS service đăng ký dưới IVehicle/default -> dùng vehicle_service có sẵn.
# KHÔNG khai service type mới, KHÔNG sửa service_contexts (tránh trùng nhãn).
allow hal_vehicle_vss vehicle_service:service_manager add;

allow hal_vehicle_vss vendor_configs_file:dir search;
allow hal_vehicle_vss vendor_configs_file:file { read getattr open };
${PERMISSIVE_LINE}
EOF
[ "$PERMISSIVE" -eq 1 ] && echo "  [warn] PERMISSIVE bật — chỉ dùng để debug, nhớ tắt trước khi chốt."

# ---- 2. file_contexts (idempotent) ---------------------------------------
echo "[2/3] Cập nhật file_contexts"
append_once "/vendor/bin/hw/${VHAL_BINARY} u:object_r:hal_vehicle_vss_exec:s0" "$FC_FILE"

# ---- 3. service_contexts: CỐ TÌNH BỎ QUA ---------------------------------
echo "[3/3] service_contexts: bỏ qua (dùng vehicle_service sẵn có cho IVehicle/default)"

echo ""
echo "Nhắc: .rc của service cần 'class early_hal' và đúng nhãn exec để init transition vào domain."
echo ""

# ---- Build + relaunch -----------------------------------------------------
if [ "$DO_BUILD" -eq 0 ]; then
  echo "Done (--no-build). Tự build & relaunch khi sẵn sàng."
  exit 0
fi

echo "==> Building selinux_policy + vendorimage..."
( cd "$AOSP_ROOT" && m selinux_policy vendorimage )

echo "==> Relaunch Cuttlefish (boot lại từ image mới)..."
if command -v cvd >/dev/null 2>&1; then
  cvd reset -y || true
  launch_cvd --daemon
elif command -v stop_cvd >/dev/null 2>&1; then
  stop_cvd || true
  launch_cvd --daemon
else
  echo "  [warn] Không thấy cvd/stop_cvd trong PATH. Tự chạy: stop_cvd && launch_cvd --daemon"
fi

echo ""
echo "Xong. Sau khi boot, gom denial:"
echo "  adb logcat -b all | grep -i avc"
echo "  # hoặc: adb shell 'dmesg | grep avc' rồi audit2allow"