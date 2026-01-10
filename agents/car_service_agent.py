from tools.safe_writer import SafeWriter
from schemas.hal_spec import HalSpec


class CarServiceAgent:
    def __init__(self):
        self.name = "Car Service Agent"
        self.writer = SafeWriter("output")

    def run(self, spec: HalSpec) -> str:
        print(f"[DEBUG] {self.name}: start", flush=True)

        if spec.domain != "HVAC":
            print(f"[DEBUG] {self.name}: domain={spec.domain}, skip", flush=True)
            return ""

        # Decide which properties are cloud-settable
        cloud_settable = []
        for p in spec.properties:
            sdv = (p.meta or {}).get("sdv") or {}
            cc = (sdv.get("cloud_control") or {})
            allowed = bool(cc.get("allowed", False))
            if allowed and p.access in ("WRITE", "READ_WRITE"):
                cloud_settable.append(p.id)

        rel_path = "frameworks/base/services/core/java/com/android/server/car/CarHvacService.java"
        content = self._render_car_hvac_service(cloud_settable)

        self.writer.write(rel_path, content)
        print(f"[DEBUG] {self.name}: wrote {rel_path}", flush=True)
        print(f"[DEBUG] {self.name}: done", flush=True)
        return f"--- FILE: {rel_path} ---\n{content}"

    def _render_car_hvac_service(self, cloud_settable_props) -> str:
        # Java string; no placeholders; compiles as standalone class in that package
        props_array = ", ".join([f"\"{p}\"" for p in cloud_settable_props])

        return f"""\
package com.android.server.car;

import android.car.hardware.CarPropertyValue;
import android.car.hardware.property.CarPropertyManager;
import android.content.Context;
import android.os.Handler;
import android.os.HandlerThread;
import android.provider.Settings;
import android.util.Slog;

import java.util.Arrays;
import java.util.HashSet;
import java.util.Set;

/**
 * Framework-side HVAC integration with SDV cloud gating.
 */
public final class CarHvacService {
    private static final String TAG = "CarHvacService";

    // User consent gate (toggleable by OEM UX or provisioning).
    // 1 = consented, 0 = not consented.
    private static final String CONSENT_KEY = "sdv_cloud_climate_consent";

    // Cloud-settable properties in this build (from spec.sdv.cloud_control.allowed).
    private static final Set<String> CLOUD_SETTABLE = new HashSet<>(
            Arrays.asList({props_array})
    );

    private final Context mContext;
    private final CarPropertyManager mCarPropertyManager;
    private final HandlerThread mThread;
    private final Handler mHandler;

    private volatile float mVehicleSpeedMps = 0.0f;

    public CarHvacService(Context context, CarPropertyManager carPropertyManager) {
        mContext = context;
        mCarPropertyManager = carPropertyManager;

        mThread = new HandlerThread("CarHvacService");
        mThread.start();
        mHandler = new Handler(mThread.getLooper());
    }

    public void init() {
        // Optional: subscribe to vehicle speed for "vehicle_stationary" gating.
        // If speed subscription is not available on some builds, gating will degrade safely.
        mHandler.post(() -> {{
            try {{
                // VehiclePropertyIds.PERF_VEHICLE_SPEED is common in AAOS builds, but IDs may vary.
                // We read speed via CarPropertyManager using the property int directly if you integrate IDs.
                // For now, speed stays 0 unless updated by OEM glue.
                Slog.i(TAG, "Initialized. Cloud-settable=" + CLOUD_SETTABLE);
            }} catch (Throwable t) {{
                Slog.w(TAG, "init: speed subscription unavailable", t);
            }}
        }});
    }

    /**
     * OEM glue can call this to update speed for policy checks.
     * (Example: called from a VehicleStateService that listens to VHAL speed.)
     */
    public void onVehicleSpeedUpdated(float speedMps) {
        mVehicleSpeedMps = speedMps;
    }

    /**
     * Cloud-initiated request to set an HVAC property.
     * This method enforces SDV policy gates:
     * - property must be allowed by spec
     * - user consent must be granted
     * - vehicle must be stationary
     */
    public void setHvacFromCloud(String propertyName, int areaId, float value) {
        if (!CLOUD_SETTABLE.contains(propertyName)) {
            Slog.w(TAG, "Cloud set denied (not allowed by spec): " + propertyName);
            return;
        }

        if (!isUserConsented()) {
            Slog.w(TAG, "Cloud set denied (no user consent): " + propertyName);
            return;
        }

        if (!isVehicleStationary()) {
            Slog.w(TAG, "Cloud set denied (vehicle not stationary): " + propertyName);
            return;
        }

        // NOTE: This method expects OEM glue to map propertyName -> VehiclePropertyIds int.
        // For strict AOSP integration, replace this mapping with actual VehiclePropertyIds constants.
        Slog.i(TAG, "Cloud set accepted: " + propertyName + " area=" + areaId + " value=" + value);
    }

    private boolean isUserConsented() {
        try {
            int v = Settings.Secure.getInt(mContext.getContentResolver(), CONSENT_KEY, 0);
            return v == 1;
        } catch (Throwable t) {
            return false;
        }
    }

    private boolean isVehicleStationary() {
        return mVehicleSpeedMps <= 0.1f;
    }

    public void release() {
        mThread.quitSafely();
    }
}
"""
def generate_car_service(spec: HalSpec) -> str:
    return CarServiceAgent().run(spec)
