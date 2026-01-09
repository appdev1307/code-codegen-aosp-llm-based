from pathlib import Path
from schemas.hal_spec import HalSpec

# Base output directory
OUTPUT_ROOT = Path("output")

# AOSP-style roots
AIDL_ROOT = OUTPUT_ROOT / "hardware/interfaces/automotive/vehicle/aidl"
HAL_ROOT = OUTPUT_ROOT / "hardware/interfaces/automotive/vehicle/impl"
FRAMEWORK_ROOT = OUTPUT_ROOT / "frameworks/base/services/core/java/com/android/server/car"
SEPOLICY_ROOT = OUTPUT_ROOT / "system/sepolicy/vendor"


def ensure_aosp_layout(spec: HalSpec):
    """
    Enforce AOSP-compliant directory layout.
    Architect owns structure, not LLM.
    """

    print("[LAYOUT] Creating AOSP output layout...")

    dirs = [
        AIDL_ROOT,
        HAL_ROOT,
        FRAMEWORK_ROOT,
        SEPOLICY_ROOT,
    ]

    for d in dirs:
        d.mkdir(parents=True, exist_ok=True)
        print(f"[LAYOUT]   ensured {d}")

    # Domain-specific folders
    domain = spec.domain.lower()

    (HAL_ROOT / domain).mkdir(exist_ok=True)
    (SEPOLICY_ROOT / domain).mkdir(exist_ok=True)

    print("[LAYOUT] AOSP layout ready.")
