# FILE: agents/build_glue_agent.py
from pathlib import Path
from tools.safe_writer import SafeWriter

class BuildGlueAgent:
    def __init__(self, output_root="output"):
        self.writer = SafeWriter(output_root)
        self.aidl_dir = "hardware/interfaces/automotive/vehicle/aidl"
        self.impl_dir = "hardware/interfaces/automotive/vehicle/impl"

    def run(self):
        print("[BUILD GLUE] Generating Soong build files and VINTF manifest...")

        # AIDL interface Android.bp
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
        self.writer.write(f"{self.aidl_dir}/Android.bp", aidl_bp)

        # C++ service Android.bp
        impl_bp = """cc_binary {
    name: "android.hardware.automotive.vehicle-service",
    relative_install_path: "hw",
    init_rc: ["android.hardware.automotive.vehicle-service.rc"],
    vintf_fragments: ["manifest_android.hardware.automotive.vehicle.xml"],
    srcs: ["VehicleHalService.cpp"],
    shared_libs: [
        "libbase",
        "libbinder_ndk",
        "liblog",
        "libutils",
        "android.hardware.automotive.vehicle-V1-ndk",
    ],
    cflags: ["-Wall", "-Werror"],
}
"""
        self.writer.write(f"{self.impl_dir}/Android.bp", impl_bp)

        # init.rc
        init_rc = """service android.hardware.automotive.vehicle-service /vendor/bin/hw/android.hardware.automotive.vehicle-service
    class hal
    user system
    group system
"""
        self.writer.write(f"{self.impl_dir}/android.hardware.automotive.vehicle-service.rc", init_rc)

        # VINTF manifest
        vintf = """<manifest version="1.0" type="device">
    <hal format="aidl">
        <name>android.hardware.automotive.vehicle</name>
        <interface>
            <name>IVehicle</name>
            <instance>default</instance>
        </interface>
    </hal>
</manifest>
"""
        self.writer.write(f"{self.impl_dir}/manifest_android.hardware.automotive.vehicle.xml", vintf)

        print("[BUILD GLUE] All build glue generated — your HAL is now AOSP-buildable!")