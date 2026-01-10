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
        props_array = ", ".join(['"%s"' % p for p in cloud_settable_props])

        template = """\
package com.android.server.car;

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
 *
 * NOTE:
 * This class demonstrates policy enforcement gates (consent + stationary) for cloud-initiated sets.
 * OEM glue should map propertyName -> VehiclePropertyIds and perform the actual CarPropertyManager set call.
 */
public final class CarHvacService {
    private static final String TAG = "CarHvacService";

    // User consent gate (1 = consented, 0 = not consented).
    private static final String CONSENT_KEY = "sdv_cloud_climate_consent";

    private static final Set<String> CLOUD_SETTABLE = new HashSet<>(
            Arrays.asList({cloud_props})
    );

    private final Context mContext;
    private final CarPropertyManager mCarPropertyManager;
    private final HandlerThread mThread;
    private final Handler mHandler;

    // Provided by OEM glue (e.g., VehicleStateService) for stationary gating.
    private volatile float mVehicleSpeedMps = 0.0f;

    public CarHvacService(Context context, CarPropertyManager carPropertyManager) {
        mContext = context;
        mCarPropertyManager = carPropertyManager;
        mThread = new HandlerThread("CarHvacService");
        mThread.start();
        mHandler = new Handler(mThread.getLooper());
    }

    public void init() {
        mHandler.post(() -> {
            Slog.i(TAG, "Initialized. Cloud-settable=" + CLOUD_SETTABLE);
        });
    }

    public void onVehicleSpeedUpdated(float speedMps) {
        mVehicleSpeedMps = speedMps;
    }

    /**
     * Cloud-initiated request to set an HVAC control.
     * Enforces SDV policy gates:
     * - property allowed by spec
     * - user consent granted
     * - vehicle stationary
     *
     * OEM glue should map propertyName to the appropriate VehiclePropertyIds constant and call CarPropertyManager.
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

        Slog.i(TAG, "Cloud set accepted: " + propertyName + " area=" + areaId + " value=" + value);

        // TODO (OEM integration):
        // int propId = mapPropertyNameToVehiclePropertyId(propertyName);
        // mCarPropertyManager.setFloatProperty(propId, areaId, value);
    }

    private boolean isUserConsented() {
        try {
            return Settings.Secure.getInt(
                    mContext.getContentResolver(), CONSENT_KEY, 0) == 1;
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
        return template.format(cloud_props=props_array)


def generate_car_service(spec: HalSpec) -> str:
    return CarServiceAgent().run(spec)
