# FILE: agents/build_glue_agent.py
from pathlib import Path
from tools.safe_writer import SafeWriter

class BuildGlueAgent:
    def __init__(self, output_root="output"):
        self.writer = SafeWriter(output_root)

    def run(self):
        print("[BUILD GLUE] Generating build files...")

        aidl_bp = """aidl_interface {
    name: "android.hardware.automotive.vehicle",
    srcs: ["android/hardware/automotive/vehicle/*.aidl"],
    stability: "vintf",
    backend: {
        cpp: { enabled: true },
        java: { enabled: true },
    },
}
"""
        self.writer.write("hardware/interfaces/automotive/vehicle/aidl/Android.bp", aidl_bp)

        impl_bp = """cc_binary {
    name: "android.hardware.automotive.vehicle-service",
    relative_install_path: "hw",
    srcs: ["VehicleHalService.cpp"],
    shared_libs: [
        "libbase",
        "libbinder_ndk",
        "liblog",
        "libutils",
        "android.hardware.automotive.vehicle-V1-ndk",
    ],
}
"""
        self.writer.write("hardware/interfaces/automotive/vehicle/impl/Android.bp", impl_bp)

        print("[BUILD GLUE] Done")