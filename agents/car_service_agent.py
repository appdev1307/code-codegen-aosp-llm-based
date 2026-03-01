from string import Template
from tools.safe_writer import SafeWriter
from schemas.hal_spec import HalSpec


# ---------------------------------------------------------------------------
# Template — every HVAC-specific string is now a variable.
# The policy logic (consent gate, stationary gate, CLOUD_SETTABLE) is generic
# and stays unchanged.
# ---------------------------------------------------------------------------
_CAR_SERVICE_TEMPLATE = Template("""\
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
 * Framework-side $domain_title integration with SDV cloud gating.
 *
 * NOTE:
 * This class demonstrates policy enforcement gates (consent + stationary)
 * for cloud-initiated sets.
 *
 * OEM glue should map propertyName -> VehiclePropertyIds and perform the
 * actual CarPropertyManager set call.
 */
public final class $class_name {
    private static final String TAG = "$class_name";

    // User consent gate (1 = consented, 0 = not consented).
    private static final String CONSENT_KEY = "$consent_key";

    private static final Set<String> CLOUD_SETTABLE = new HashSet<>(
            Arrays.asList($cloud_props)
    );

    private final Context mContext;
    private final CarPropertyManager mCarPropertyManager;
    private final HandlerThread mThread;
    private final Handler mHandler;

    // Provided by OEM glue (e.g., VehicleStateService) for stationary gating.
    private volatile float mVehicleSpeedMps = 0.0f;

    public $class_name(Context context, CarPropertyManager carPropertyManager) {
        mContext = context;
        mCarPropertyManager = carPropertyManager;
        mThread = new HandlerThread("$class_name");
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
     * Cloud-initiated request to set a $domain_title control.
     * Enforces SDV policy gates:
     * - property allowed by spec
     * - user consent granted
     * - vehicle stationary
     *
     * OEM glue should map propertyName to the appropriate VehiclePropertyIds
     * constant and call CarPropertyManager.
     */
    public void set${domain_title}FromCloud(String propertyName, int areaId, float value) {
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

        Slog.i(TAG, "Cloud set accepted: " + propertyName
                + " area=" + areaId + " value=" + value);

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
""")


class CarServiceAgent:
    def __init__(self, output_root: str = "output"):
        self.name = "Car Service Agent"
        self.writer = SafeWriter(output_root)

    def run(self, spec: HalSpec) -> str:
        domain = (getattr(spec, "domain", "") or "").strip().upper()
        if not domain or domain == "UNKNOWN":
            print(f"[{self.name}] No valid domain on spec, skipping", flush=True)
            return ""

        print(f"[{self.name}] domain={domain}", flush=True)

        # Derive all the names from the domain
        domain_title = domain.capitalize()          # Hvac, Powertrain, Chassis …
        domain_lower = domain.lower()               # hvac, powertrain, chassis …
        class_name   = f"Car{domain_title}Service"  # CarHvacService, CarPowertrainService …
        consent_key  = f"sdv_cloud_{domain_lower}_consent"  # sdv_cloud_hvac_consent …

        # Cloud-settable property extraction — already generic, works for any domain
        cloud_settable = []
        for p in spec.properties:
            sdv = (p.meta or {}).get("sdv") or {}
            cc = sdv.get("cloud_control") or {}
            allowed = bool(cc.get("allowed", False))
            if allowed and p.access in ("WRITE", "READ_WRITE"):
                cloud_settable.append(p.id)

        # Render
        props_array = ", ".join(f'"{p}"' for p in cloud_settable)
        content = _CAR_SERVICE_TEMPLATE.substitute(
            class_name=class_name,
            domain_title=domain_title,
            domain_lower=domain_lower,
            consent_key=consent_key,
            cloud_props=props_array,
        )

        # Output path follows the same pattern for every domain
        rel_path = (
            f"frameworks/base/services/core/java/"
            f"com/android/server/car/{class_name}.java"
        )

        self.writer.write(rel_path, content)
        print(f"[{self.name}] wrote {rel_path}", flush=True)

        return f"--- FILE: {rel_path} ---\n{content}"


def generate_car_service(spec: HalSpec, output_root: str = "output") -> str:
    return CarServiceAgent(output_root=output_root).run(spec)