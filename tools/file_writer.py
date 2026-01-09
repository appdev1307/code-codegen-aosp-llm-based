from pathlib import Path
from schemas.hal_spec import HalSpec
from tools.aosp_layout import (
    AIDL_ROOT,
    HAL_ROOT,
    FRAMEWORK_ROOT,
    SEPOLICY_ROOT,
)


def write_outputs(spec: HalSpec, artifacts: dict):
    print("[WRITER] Writing generated files to disk...")

    # ---------- AIDL ----------
    aidl_path = (
        AIDL_ROOT / "IVehicle.aidl"
    )
    aidl_path.write_text(artifacts["aidl"])
    print(f"[WRITER]   ✔ {aidl_path}")

    # ---------- HAL Service ----------
    hal_cpp = HAL_ROOT / f"{spec.domain.capitalize()}VehicleHal.cpp"
    hal_cpp.write_text(artifacts["vhal"])
    print(f"[WRITER]   ✔ {hal_cpp}")

    # ---------- Car Service ----------
    car_java = (
        FRAMEWORK_ROOT
        / f"Car{spec.domain.capitalize()}Service.java"
    )
    car_java.write_text(artifacts["car"])
    print(f"[WRITER]   ✔ {car_java}")

    # ---------- SELinux ----------
    sepolicy = SEPOLICY_ROOT / f"hal_{spec.domain.lower()}.te"
    sepolicy.write_text(artifacts["sepolicy"])
    print(f"[WRITER]   ✔ {sepolicy}")

    print("[WRITER] File generation completed ✅")
