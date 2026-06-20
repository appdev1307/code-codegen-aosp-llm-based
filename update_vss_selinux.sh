#!/bin/bash
# update_vss_selinux.sh
# Chạy trên HOST sau khi Cuttlefish boot xong. Verify VSS VHAL lên đúng.
# Usage: ./post_boot_check.sh

set -uo pipefail

BIN="/vendor/bin/hw/android.hardware.automotive.vehicle@V3-vss-service"
EXPECT_LABEL="u:object_r:hal_vehicle_vss_exec:s0"
IFACE="android.hardware.automotive.vehicle.IVehicle/default"

pass() { printf "  \033[32mPASS\033[0m  %s\n" "$1"; }
fail() { printf "  \033[31mFAIL\033[0m  %s\n" "$1"; }
info() { printf "  ----  %s\n" "$1"; }

# Đợi device sẵn sàng
echo "==> Chờ adb device..."
adb wait-for-device
adb shell 'while [ "$(getprop sys.boot_completed)" != 1 ]; do sleep 1; done'
echo ""

# 1. SELinux label trên binary -------------------------------------------
echo "[1] SELinux label của binary"
LABEL=$(adb shell ls -Z "$BIN" 2>/dev/null | awk '{print $1}')
if [ "$LABEL" = "$EXPECT_LABEL" ]; then
  pass "label = $LABEL"
else
  fail "label = ${LABEL:-<không thấy file>}  (kỳ vọng $EXPECT_LABEL)"
  info "=> file_contexts regex không khớp tên binary; init_daemon_domain sẽ không transition."
fi
echo ""

# 2. Domain của process đang chạy ----------------------------------------
echo "[2] Process có chạy trong domain hal_vehicle_vss không"
PS=$(adb shell ps -AZ 2>/dev/null | grep 'vehicle@V3-vss-service' || true)
if [ -n "$PS" ]; then
  if echo "$PS" | grep -q 'hal_vehicle_vss'; then
    pass "process trong domain hal_vehicle_vss"
  else
    fail "process chạy nhưng SAI domain:"
    echo "$PS" | sed 's/^/        /'
  fi
else
  fail "không thấy process -> service chưa start (xem mục 4 & 5)"
fi
echo ""

# 3. Service đã đăng ký với servicemanager --------------------------------
echo "[3] Service registration ($IFACE)"
LSHAL=$(adb shell lshal 2>/dev/null | grep -i 'vehicle' | grep -i 'IVehicle/default' || true)
if [ -n "$LSHAL" ]; then
  pass "lshal thấy IVehicle/default"
  echo "$LSHAL" | sed 's/^/        /'
else
  fail "lshal KHÔNG thấy IVehicle/default -> addService thất bại (thường do AVC deny)"
fi
echo ""

# 4. CarService nhận được VHAL -------------------------------------------
echo "[4] CarService"
CAR=$(adb shell dumpsys car_service 2>/dev/null | head -n 5 || true)
if [ -n "$CAR" ]; then
  pass "car_service phản hồi"
else
  info "car_service chưa sẵn sàng (có thể đang khởi động, thử lại sau vài giây)"
fi
echo ""

# 5. AVC denials ----------------------------------------------------------
echo "[5] SELinux denials liên quan"
AVC=$(adb shell 'dmesg' 2>/dev/null | grep -i 'avc:' | grep -iE 'hal_vehicle_vss|vss-service' || true)
if [ -z "$AVC" ]; then
  pass "không có denial cho hal_vehicle_vss"
else
  fail "có denial — service có thể bị chặn:"
  echo "$AVC" | sed 's/^/        /'
  info "=> audit2allow các dòng trên, fold vào hal_vehicle_vss.te rồi rebuild."
fi
echo ""

echo "==> Xong. Nếu mục 1-3 PASS là service đã lên đúng domain và registration."