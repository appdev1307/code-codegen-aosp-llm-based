#!/usr/bin/env python3
"""
multi_main_c5_hmi.py
═══════════════════════════════════════════════════════════════════
Condition 5 — HMI App Generation with Real AOSP Property IDs

This pipeline:
  1. Reads compiled property IDs from AOSP build output (VehicleProperty*.aidl dumps)
  2. Maps VSS signals to nearest AOSP VehiclePropertyIds for Cuttlefish compatibility
  3. Uses C4 optimised DSPy programs to generate Kotlin fragments + XML layouts
  4. Outputs a complete Android Automotive app that works on Cuttlefish

NO labelling, NO AIDL/C++/SELinux/bp generation — app only.

Usage (on Colab):
    python multi_main_c5_hmi.py

Requirements:
  - AOSP build output at AOSP_DUMP_DIR (from GCP VM, copied via GCS)
  - C4 DSPy programs at dspy_opt/saved/
  - Ollama running with qwen2.5-coder:32b
═══════════════════════════════════════════════════════════════════
"""

from __future__ import annotations

import json
import os
import re
import time
from pathlib import Path

# ── Configuration ────────────────────────────────────────────────
OUTPUT_DIR      = Path("output_c5_hmi")
DSPY_SAVED_DIR  = "dspy_opt/saved"
RAG_DB_PATH     = "rag/chroma_db"
RAG_TOP_K       = 8

# Path to AOSP compiled AIDL dump (copy from GCP VM or provide locally)
# On GCP VM:
#   out/soong/.intermediates/hardware/interfaces/automotive/vehicle/aidl/
#   android.hardware.automotive.vehicle-api/dump/android/hardware/automotive/vehicle/
AOSP_DUMP_DIR   = Path("aosp_dump")   # local copy of the dump directory

# ── Domain base addresses (must match rag_dspy_aidl_agent.py) ───
DOMAIN_BASE = {
    "adas":          0x1000,
    "body":          0x2000,
    "cabin":         0x3000,
    "chassis":       0x4000,
    "hvac":          0x5000,
    "infotainment":  0x6000,
    "powertrain":    0x7000,
}

# ── AOSP VehiclePropertyIds that Cuttlefish FakeVehicleHardware serves ──
# These are real integer property IDs from android.car.VehiclePropertyIds
# and android.hardware.automotive.vehicle.VehicleProperty
# Mapping: VSS domain → list of (property_name, int_id, type, access, description)
AOSP_PROPERTY_MAP = {
    "adas": [
        ("AUTOMATIC_EMERGENCY_BRAKING_ENABLED",   0x1040A40B, "BOOLEAN",  "READ_WRITE", "AEB enabled"),
        ("AUTOMATIC_EMERGENCY_BRAKING_STATE",      0x1041440B, "INT32",    "READ",       "AEB state"),
        ("FORWARD_COLLISION_WARNING_ENABLED",      0x1042A40B, "BOOLEAN",  "READ_WRITE", "FCW enabled"),
        ("FORWARD_COLLISION_WARNING_STATE",        0x1043440B, "INT32",    "READ",       "FCW state"),
        ("BLIND_SPOT_WARNING_ENABLED",             0x1044A40B, "BOOLEAN",  "READ_WRITE", "BSW enabled"),
        ("BLIND_SPOT_WARNING_STATE",               0x1045440B, "INT32",    "READ",       "BSW state"),
        ("LANE_DEPARTURE_WARNING_ENABLED",         0x1046A40B, "BOOLEAN",  "READ_WRITE", "LDW enabled"),
        ("LANE_DEPARTURE_WARNING_STATE",           0x1047440B, "INT32",    "READ",       "LDW state"),
        ("LANE_KEEP_ASSIST_ENABLED",               0x1048A40B, "BOOLEAN",  "READ_WRITE", "LKA enabled"),
        ("LANE_KEEP_ASSIST_STATE",                 0x1049440B, "INT32",    "READ",       "LKA state"),
        ("CRUISE_CONTROL_ENABLED",                 0x1050A40B, "BOOLEAN",  "READ_WRITE", "CC enabled"),
        ("CRUISE_CONTROL_STATE",                   0x1051440B, "INT32",    "READ",       "CC state"),
        ("CRUISE_CONTROL_TARGET_SPEED",            0x1052604B, "FLOAT",    "READ_WRITE", "CC target speed"),
        ("HANDS_ON_DETECTION_ENABLED",             0x1053A40B, "BOOLEAN",  "READ_WRITE", "HOD enabled"),
        ("HANDS_ON_DETECTION_DRIVER_STATE",        0x1054440B, "INT32",    "READ",       "HOD driver state"),
        ("EMERGENCY_LANE_KEEP_ASSIST_ENABLED",     0x1055A40B, "BOOLEAN",  "READ_WRITE", "ELKA enabled"),
    ],
    "body": [
        ("HIGH_BEAM_LIGHTS_STATE",    0x11400F02, "INT32",   "READ",       "High beam state"),
        ("HIGH_BEAM_LIGHTS_SWITCH",   0x11410A02, "INT32",   "READ_WRITE", "High beam switch"),
        ("HAZARD_LIGHTS_STATE",       0x11420F02, "INT32",   "READ",       "Hazard lights state"),
        ("HAZARD_LIGHTS_SWITCH",      0x11430A02, "INT32",   "READ_WRITE", "Hazard lights switch"),
        ("CABIN_LIGHTS_STATE",        0x11500F02, "INT32",   "READ",       "Cabin lights state"),
        ("CABIN_LIGHTS_SWITCH",       0x11510A02, "INT32",   "READ_WRITE", "Cabin lights switch"),
        ("READING_LIGHTS_STATE",      0x11520F12, "INT32",   "READ",       "Reading lights state"),
        ("READING_LIGHTS_SWITCH",     0x11530A12, "INT32",   "READ_WRITE", "Reading lights switch"),
        ("VEHICLE_CURB_WEIGHT",       0x11600602, "INT32",   "READ",       "Curb weight kg"),
        ("DOOR_LOCK",                 0x11640A16, "BOOLEAN", "READ_WRITE", "Door lock state"),
        ("DOOR_MOVE",                 0x11650A16, "INT32",   "READ_WRITE", "Door move"),
        ("DOOR_POS",                  0x11660A16, "INT32",   "READ",       "Door position"),
        ("MIRROR_LOCK",               0x11700A02, "BOOLEAN", "READ_WRITE", "Mirror lock"),
        ("MIRROR_FOLD",               0x11710A02, "BOOLEAN", "READ_WRITE", "Mirror fold"),
        ("MIRROR_Y_POS",              0x11720A12, "INT32",   "READ_WRITE", "Mirror Y position"),
        ("MIRROR_Z_POS",              0x11730A12, "INT32",   "READ_WRITE", "Mirror Z position"),
    ],
    "cabin": [
        ("HVAC_FAN_SPEED",            0x12400A1F, "INT32",   "READ_WRITE", "HVAC fan speed"),
        ("HVAC_FAN_DIRECTION",        0x12410A1F, "INT32",   "READ_WRITE", "HVAC fan direction"),
        ("HVAC_TEMPERATURE_CURRENT",  0x1242061F, "FLOAT",   "READ",       "HVAC current temp"),
        ("HVAC_TEMPERATURE_SET",      0x1243661F, "FLOAT",   "READ_WRITE", "HVAC set temp"),
        ("HVAC_AC_ON",                0x12440A1F, "BOOLEAN", "READ_WRITE", "HVAC AC on"),
        ("HVAC_MAX_AC_ON",            0x12450A1F, "BOOLEAN", "READ_WRITE", "HVAC max AC"),
        ("HVAC_MAX_DEFROST_ON",       0x12460A1F, "BOOLEAN", "READ_WRITE", "HVAC max defrost"),
        ("HVAC_RECIRC_ON",            0x12470A1F, "BOOLEAN", "READ_WRITE", "HVAC recirculate"),
        ("HVAC_DUAL_ON",              0x12480A1F, "BOOLEAN", "READ_WRITE", "HVAC dual zone"),
        ("HVAC_AUTO_ON",              0x12490A1F, "BOOLEAN", "READ_WRITE", "HVAC auto mode"),
        ("HVAC_DEFROSTER",            0x124A0A13, "BOOLEAN", "READ_WRITE", "HVAC defroster"),
        ("SEAT_OCCUPANCY",            0x12B00A10, "INT32",   "READ",       "Seat occupancy"),
        ("SEAT_BELT_BUCKLED",         0x12B10A10, "BOOLEAN", "READ",       "Seat belt buckled"),
        ("SEAT_HEADREST_HEIGHT_MOVE", 0x12B40A10, "INT32",   "READ_WRITE", "Headrest height"),
        ("SEAT_DEPTH_MOVE",           0x12B50A10, "INT32",   "READ_WRITE", "Seat depth"),
        ("SEAT_HEIGHT_MOVE",          0x12B60A10, "INT32",   "READ_WRITE", "Seat height"),
    ],
    "chassis": [
        ("PERF_VEHICLE_SPEED",        0x11600207, "FLOAT",   "READ",       "Vehicle speed m/s"),
        ("PERF_VEHICLE_SPEED_DISPLAY",0x11600307, "FLOAT",   "READ",       "Display speed"),
        ("PERF_STEERING_ANGLE",       0x11600107, "FLOAT",   "READ",       "Steering angle deg"),
        ("PERF_REAR_STEERING_ANGLE",  0x11600807, "FLOAT",   "READ",       "Rear steering angle"),
        ("PERF_ODOMETER",             0x11600407, "FLOAT",   "READ",       "Odometer km"),
        ("WHEEL_TICK",                0x11600507, "INT64",   "READ",       "Wheel tick count"),
        ("ABS_ACTIVE",                0x11610207, "BOOLEAN", "READ",       "ABS active"),
        ("TRACTION_CONTROL_ACTIVE",   0x11620207, "BOOLEAN", "READ",       "TC active"),
        ("ESC_IN_PROGRESS",           0x11630207, "BOOLEAN", "READ",       "ESC in progress"),
        ("TIRE_PRESSURE",             0x11640607, "FLOAT",   "READ",       "Tire pressure kPa"),
        ("CRITICALLY_LOW_TIRE_PRESSURE", 0x11641607, "FLOAT","READ",       "Critical tire pressure"),
        ("PARKING_BRAKE_ON",          0x11650207, "BOOLEAN", "READ_WRITE", "Parking brake"),
        ("PARKING_BRAKE_AUTO_APPLY",  0x11660207, "BOOLEAN", "READ_WRITE", "Auto parking brake"),
        ("VEHICLE_DRIVING_AUTOMATION_CURRENT_LEVEL", 0x11700207, "INT32", "READ", "Automation level"),
        ("GEAR_SELECTION",            0x11410207, "INT32",   "READ_WRITE", "Gear selection"),
        ("CURRENT_GEAR",              0x11420207, "INT32",   "READ",       "Current gear"),
    ],
    "hvac": [
        ("HVAC_FAN_SPEED",            0x12400A1F, "INT32",   "READ_WRITE", "Fan speed 1-7"),
        ("HVAC_FAN_DIRECTION",        0x12410A1F, "INT32",   "READ_WRITE", "Fan direction"),
        ("HVAC_TEMPERATURE_CURRENT",  0x1242061F, "FLOAT",   "READ",       "Current temp C"),
        ("HVAC_TEMPERATURE_SET",      0x1243661F, "FLOAT",   "READ_WRITE", "Set temp C"),
        ("HVAC_AC_ON",                0x12440A1F, "BOOLEAN", "READ_WRITE", "AC on/off"),
        ("HVAC_MAX_AC_ON",            0x12450A1F, "BOOLEAN", "READ_WRITE", "Max AC"),
        ("HVAC_MAX_DEFROST_ON",       0x12460A1F, "BOOLEAN", "READ_WRITE", "Max defrost"),
        ("HVAC_RECIRC_ON",            0x12470A1F, "BOOLEAN", "READ_WRITE", "Recirculate"),
        ("HVAC_DUAL_ON",              0x12480A1F, "BOOLEAN", "READ_WRITE", "Dual zone"),
        ("HVAC_AUTO_ON",              0x12490A1F, "BOOLEAN", "READ_WRITE", "Auto HVAC"),
        ("HVAC_POWER_ON",             0x12500A1F, "BOOLEAN", "READ_WRITE", "HVAC power"),
        ("HVAC_DEFROSTER",            0x124A0A13, "BOOLEAN", "READ_WRITE", "Defroster"),
        ("HVAC_STEERING_WHEEL_HEAT",  0x1251060F, "INT32",   "READ_WRITE", "Steering wheel heat"),
        ("HVAC_SEAT_TEMPERATURE",     0x1252661F, "FLOAT",   "READ_WRITE", "Seat temp"),
        ("HVAC_SEAT_VENTILATION",     0x1253060F, "INT32",   "READ_WRITE", "Seat ventilation"),
        ("HVAC_ELECTRIC_DEFROSTER_ON",0x1254A413, "BOOLEAN", "READ_WRITE", "Electric defroster"),
        ("HVAC_TEMPERATURE_DISPLAY_UNITS", 0x1256040F, "INT32", "READ_WRITE", "Temp display units"),
        ("HVAC_ACTUAL_FAN_SPEED_RPM", 0x1257060F, "INT32",   "READ",       "Fan RPM"),
        ("HVAC_SIDE_MIRROR_HEAT",     0x1258060F, "INT32",   "READ_WRITE", "Mirror heat"),
        ("HVAC_AUTO_RECIRC_ON",       0x12590A1F, "BOOLEAN", "READ_WRITE", "Auto recirculate"),
    ],
    "infotainment": [
        ("DISTANCE_DISPLAY_UNITS",    0x11400604, "INT32",   "READ_WRITE", "Distance units"),
        ("FUEL_VOLUME_DISPLAY_UNITS",  0x11410604, "INT32",   "READ_WRITE", "Fuel volume units"),
        ("TIRE_PRESSURE_DISPLAY_UNITS",0x11420604, "INT32",   "READ_WRITE", "Tire pressure units"),
        ("EV_BATTERY_DISPLAY_UNITS",   0x11430604, "INT32",   "READ_WRITE", "EV battery units"),
        ("VEHICLE_SPEED_DISPLAY_UNITS",0x11440604, "INT32",   "READ_WRITE", "Speed units"),
        ("EPOCH_TIME",                 0x11600604, "INT64",   "READ_WRITE", "Unix epoch time ms"),
        ("CLUSTER_SWITCH_UI",          0x11500A04, "INT32",   "READ_WRITE", "Cluster UI switch"),
        ("CLUSTER_DISPLAY_STATE",      0x11510604, "INT32",   "READ",       "Cluster display state"),
        ("CLUSTER_REPORT_STATE",       0x11520604, "INT32",   "READ",       "Cluster report state"),
        ("CLUSTER_REQUEST_DISPLAY",    0x11530604, "INT32",   "READ",       "Cluster request"),
        ("CLUSTER_NAVIGATION_STATE",   0x11540604, "BYTES",   "READ",       "Navigation state"),
        ("DISPLAY_BRIGHTNESS",         0x11400104, "INT32",   "READ_WRITE", "Display brightness"),
        ("AP_POWER_STATE_REPORT",      0x11500104, "INT32_VEC","READ_WRITE","AP power state"),
        ("AP_POWER_STATE_REQ",         0x11510104, "INT32_VEC","READ",      "AP power request"),
        ("AP_POWER_BOOTUP_REASON",     0x11520104, "INT32",   "READ",       "Boot reason"),
        ("VEHICLE_MAP_SERVICE",        0x11600004, "BYTES",   "READ_WRITE", "VMS message"),
        ("OBD2_LIVE_FRAME",            0x11600004, "MIXED",   "READ",       "OBD2 live frame"),
        ("SHUTDOWN_REQUEST",           0x11540104, "INT32",   "READ",       "Shutdown request"),
        ("VEHICLE_IN_USE",             0x11550A04, "BOOLEAN", "READ_WRITE", "Vehicle in use"),
        ("AUTOMATIC_EMERGENCY_BRAKING_ENABLED", 0x1040A40B, "BOOLEAN", "READ_WRITE", "AEB (via infotainment)"),
    ],
    "powertrain": [
        ("ENGINE_RPM",                 0x11600101, "FLOAT",   "READ",       "Engine RPM"),
        ("ENGINE_COOLANT_TEMP",        0x11610601, "FLOAT",   "READ",       "Coolant temp C"),
        ("ENGINE_OIL_LEVEL",           0x11620401, "INT32",   "READ",       "Oil level"),
        ("ENGINE_OIL_TEMP",            0x11630601, "FLOAT",   "READ",       "Oil temp C"),
        ("ENGINE_ON_TIME",             0x11640601, "INT64",   "READ",       "Engine on time ms"),
        ("FUEL_LEVEL",                 0x11650601, "FLOAT",   "READ",       "Fuel level %"),
        ("FUEL_DOOR_OPEN",             0x11660A01, "BOOLEAN", "READ_WRITE", "Fuel door open"),
        ("EV_BATTERY_LEVEL",           0x11700601, "FLOAT",   "READ",       "EV battery %"),
        ("EV_CURRENT_BATTERY_CAPACITY",0x11710601, "FLOAT",   "READ",       "Battery capacity Wh"),
        ("EV_CHARGE_PORT_OPEN",        0x11720A01, "BOOLEAN", "READ_WRITE", "Charge port open"),
        ("EV_CHARGE_PORT_CONNECTED",   0x11730201, "BOOLEAN", "READ",       "Charge port connected"),
        ("EV_BATTERY_INSTANTANEOUS_CHARGE_RATE", 0x11740601, "FLOAT", "READ", "Charge rate mW"),
        ("RANGE_REMAINING",            0x11750601, "FLOAT",   "READ_WRITE", "Range remaining m"),
        ("EV_CHARGE_CURRENT_DRAW_LIMIT",0x11760601,"FLOAT",  "READ_WRITE", "Charge draw limit A"),
        ("EV_CHARGE_PERCENT_LIMIT",    0x11770601, "FLOAT",   "READ_WRITE", "Charge % limit"),
        ("EV_CHARGE_STATE",            0x11780401, "INT32",   "READ",       "Charge state"),
        ("EV_CHARGE_SWITCH",           0x11790A01, "BOOLEAN", "READ_WRITE", "Charge switch"),
        ("EV_ESTIMATED_CHARGE_DURATION_MILLIS", 0x117A0601, "INT32", "READ", "Charge duration ms"),
        ("EV_PRECONDITION_REQUEST",    0x117B0A01, "BOOLEAN", "READ_WRITE", "Precondition request"),
        ("TRANSMISSION_CURRENT_GEAR",  0x11410101, "INT32",   "READ",       "Transmission gear"),
    ],
}

# ── LLM client ───────────────────────────────────────────────────
def _call_llm(prompt: str, timeout: int = 180) -> str:
    try:
        from llm_client import call_llm
        try:
            return call_llm(prompt, timeout=timeout)
        except TypeError:
            return call_llm(prompt)
    except Exception as e:
        print(f"  [LLM] Error: {e}")
        return ""

# ── DSPy app generation ──────────────────────────────────────────
def _load_dspy_app_program():
    """Load C4 optimised android_app DSPy program."""
    try:
        import dspy
        from dspy_opt.hal_modules import MODULE_REGISTRY
        prog_path = Path(DSPY_SAVED_DIR) / "android_app_program" / "program.json"
        if not prog_path.exists():
            print(f"  [C5] No saved DSPy app program at {prog_path} — using direct LLM")
            return None
        module_cls = MODULE_REGISTRY["android_app"][2]
        prog = module_cls()
        prog.load(str(prog_path))
        print(f"  [C5] Loaded DSPy android_app program ✓")
        return prog
    except Exception as e:
        print(f"  [C5] DSPy load failed: {e} — using direct LLM")
        return None

def _generate_fragment_dspy(prog, domain: str, properties: list) -> str:
    """Generate Kotlin fragment using DSPy program."""
    prop_spec = _format_properties(domain, properties)
    try:
        result = prog(
            domain=domain.upper(),
            properties=prop_spec,
            aosp_context=_get_rag_context(domain),
        )
        return getattr(result, "android_app_code", "") or ""
    except Exception as e:
        print(f"  [C5] DSPy generation failed for {domain}: {e}")
        return ""

def _get_rag_context(domain: str) -> str:
    """Retrieve RAG context for CarPropertyManager examples."""
    try:
        from rag.aosp_retriever import get_retriever
        retriever = get_retriever(db_path=RAG_DB_PATH, top_k=RAG_TOP_K)
        query = f"CarPropertyManager getProperty registerCallback {domain} automotive"
        chunks = retriever.retrieve(query, collection="aosp_car_api")
        return "\n\n".join(c.get("document", "") for c in chunks[:4])
    except Exception as e:
        print(f"  [C5] RAG retrieval failed: {e}")
        return ""

def _format_properties(domain: str, properties: list) -> str:
    """Format property list for LLM prompt."""
    lines = [f"Domain: {domain.upper()}", "Properties (name, int_id, type, access, description):"]
    for name, prop_id, typ, access, desc in properties:
        lines.append(f"  - {name} = {hex(prop_id)} ({typ}, {access}) // {desc}")
    return "\n".join(lines)

# ── Kotlin fragment generation ───────────────────────────────────
def _generate_fragment(domain: str, properties: list, prog=None) -> str:
    """Generate Kotlin Fragment using DSPy or direct LLM."""
    class_name = f"{domain.capitalize()}Fragment"
    prop_spec   = _format_properties(domain, properties)

    if prog:
        result = _generate_fragment_dspy(prog, domain, properties)
        if result and len(result) > 200:
            return result

    # Direct LLM fallback
    prop_lines = []
    for name, prop_id, typ, access, desc in properties:
        prop_lines.append(
            f"    // {desc}\n"
            f"    private val PROP_{name} = {prop_id}  // {hex(prop_id)}"
        )
    props_str = "\n".join(prop_lines)

    read_write_props = [(n, i, t, a, d) for n, i, t, a, d in properties if "WRITE" in a]
    read_only_props  = [(n, i, t, a, d) for n, i, t, a, d in properties if a == "READ"]

    ui_setup_lines = []
    for name, prop_id, typ, access, desc in read_write_props[:5]:
        if typ == "BOOLEAN":
            ui_setup_lines.append(
                f"        binding.switch{name.replace('_','').capitalize()}.setOnCheckedChangeListener {{ _, checked ->\n"
                f"            carPropertyManager?.setBooleanProperty(PROP_{name}, 0, checked)\n"
                f"        }}"
            )
        elif typ in ("FLOAT",):
            ui_setup_lines.append(
                f"        binding.slider{name.replace('_','').capitalize()}.addOnChangeListener {{ _, value, _ ->\n"
                f"            carPropertyManager?.setFloatProperty(PROP_{name}, 0, value)\n"
                f"        }}"
            )

    callback_lines = []
    for name, prop_id, typ, access, desc in properties[:8]:
        if typ == "BOOLEAN":
            callback_lines.append(
                f"            {prop_id} -> binding.tv{name.replace('_','').capitalize()}.text = "
                f"\"${{value.value as Boolean}}\""
            )
        elif typ == "FLOAT":
            callback_lines.append(
                f"            {prop_id} -> binding.tv{name.replace('_','').capitalize()}.text = "
                f"\"${{\"%.2f\".format(value.value as Float)}}\""
            )
        else:
            callback_lines.append(
                f"            {prop_id} -> binding.tv{name.replace('_','').capitalize()}.text = "
                f"\"${{value.value}}\""
            )

    prompt = f"""Generate a complete Android Automotive OS Kotlin Fragment for the {domain.upper()} domain.

Properties (use these exact integer IDs with CarPropertyManager):
{prop_spec}

Requirements:
- Class name: {class_name}
- Package: com.vss.vehicleapp.fragments
- Use CarPropertyManager with the exact integer property IDs above
- Register CarPropertyEventCallback for each property
- READ_WRITE properties: use Switch (boolean) or SeekBar (numeric) with setProperty calls
- READ-only properties: display in TextView
- Use ViewBinding (binding variable)
- Handle CarNotConnectedException
- Implement onCreateView, onViewCreated, onDestroyView lifecycle
- Use car.getCarManager(Car.PROPERTY_SERVICE) to get CarPropertyManager

Generate ONLY the Kotlin code, no markdown fences."""

    result = _call_llm(prompt, timeout=180)
    if result and "class " in result:
        return result

    # Template fallback
    return f"""package com.vss.vehicleapp.fragments

import android.car.Car
import android.car.hardware.CarPropertyValue
import android.car.hardware.property.CarPropertyEventCallback
import android.car.hardware.property.CarPropertyManager
import android.os.Bundle
import android.view.LayoutInflater
import android.view.View
import android.view.ViewGroup
import androidx.fragment.app.Fragment
import com.vss.vehicleapp.databinding.Fragment{domain.capitalize()}Binding

class {class_name} : Fragment() {{

    private var _binding: Fragment{domain.capitalize()}Binding? = null
    private val binding get() = _binding!!
    private var car: Car? = null
    private var carPropertyManager: CarPropertyManager? = null

{props_str}

    private val propertyCallback = object : CarPropertyEventCallback {{
        override fun onChangeEvent(value: CarPropertyValue<*>) {{
            activity?.runOnUiThread {{
                when (value.propertyId) {{
{chr(10).join('                    ' + l for l in callback_lines)}
                    else -> {{}}
                }}
            }}
        }}
        override fun onErrorEvent(propId: Int, zone: Int) {{
            // Handle error
        }}
    }}

    override fun onCreateView(inflater: LayoutInflater, container: ViewGroup?, savedInstanceState: Bundle?): View {{
        _binding = Fragment{domain.capitalize()}Binding.inflate(inflater, container, false)
        return binding.root
    }}

    override fun onViewCreated(view: View, savedInstanceState: Bundle?) {{
        super.onViewCreated(view, savedInstanceState)
        try {{
            car = Car.createCar(requireContext())
            carPropertyManager = car?.getCarManager(Car.PROPERTY_SERVICE) as? CarPropertyManager

            // Register callbacks for all properties
            {chr(10).join(f'carPropertyManager?.registerCallback(propertyCallback, PROP_{n}, CarPropertyManager.SENSOR_RATE_ONCHANGE)' for n, i, t, a, d in properties[:10])}

{chr(10).join('            ' + l for l in ui_setup_lines)}

        }} catch (e: Exception) {{
            e.printStackTrace()
        }}
    }}

    override fun onDestroyView() {{
        super.onDestroyView()
        carPropertyManager?.unregisterCallback(propertyCallback)
        car?.disconnect()
        _binding = null
    }}
}}
"""

# ── XML layout generation ────────────────────────────────────────
def _generate_layout(domain: str, properties: list) -> str:
    """Generate Android XML layout for the domain fragment."""
    items = []
    for name, prop_id, typ, access, desc in properties:
        safe_id = name.lower().replace("_", "")
        label = desc.replace("&", "&amp;")

        if "WRITE" in access and typ == "BOOLEAN":
            items.append(f"""
    <LinearLayout android:layout_width="match_parent" android:layout_height="wrap_content"
        android:orientation="horizontal" android:padding="4dp">
        <TextView android:layout_width="0dp" android:layout_height="wrap_content"
            android:layout_weight="1" android:text="{label}" android:textSize="14sp"/>
        <Switch android:id="@+id/switch{safe_id}" android:layout_width="wrap_content"
            android:layout_height="wrap_content"/>
    </LinearLayout>""")
        elif "WRITE" in access and typ == "FLOAT":
            items.append(f"""
    <LinearLayout android:layout_width="match_parent" android:layout_height="wrap_content"
        android:orientation="vertical" android:padding="4dp">
        <TextView android:layout_width="match_parent" android:layout_height="wrap_content"
            android:text="{label}" android:textSize="14sp"/>
        <SeekBar android:id="@+id/slider{safe_id}" android:layout_width="match_parent"
            android:layout_height="wrap_content" android:max="100"/>
    </LinearLayout>""")
        else:
            items.append(f"""
    <LinearLayout android:layout_width="match_parent" android:layout_height="wrap_content"
        android:orientation="horizontal" android:padding="4dp">
        <TextView android:layout_width="0dp" android:layout_height="wrap_content"
            android:layout_weight="1" android:text="{label}:" android:textSize="14sp"/>
        <TextView android:id="@+id/tv{safe_id}" android:layout_width="wrap_content"
            android:layout_height="wrap_content" android:text="--" android:textSize="14sp"
            android:textStyle="bold"/>
    </LinearLayout>""")

    items_xml = "\n".join(items)
    return f"""<?xml version="1.0" encoding="utf-8"?>
<ScrollView xmlns:android="http://schemas.android.com/apk/res/android"
    android:layout_width="match_parent"
    android:layout_height="match_parent">
    <LinearLayout
        android:layout_width="match_parent"
        android:layout_height="wrap_content"
        android:orientation="vertical"
        android:padding="16dp">

        <TextView android:layout_width="match_parent" android:layout_height="wrap_content"
            android:text="{domain.upper()} Properties" android:textSize="18sp"
            android:textStyle="bold" android:paddingBottom="8dp"/>
{items_xml}

    </LinearLayout>
</ScrollView>
"""

# ── MainActivity generation ──────────────────────────────────────
def _generate_main_activity(domains: list) -> str:
    imports = "\n".join(
        f"import com.vss.vehicleapp.fragments.{d.capitalize()}Fragment"
        for d in domains
    )
    tab_items = "\n".join(
        f'            tabLayout.addTab(tabLayout.newTab().setText("{d.upper()}"))'
        for d in domains
    )
    fragments_list = ", ".join(f"{d.capitalize()}Fragment()" for d in domains)

    return f"""package com.vss.vehicleapp

import android.os.Bundle
import androidx.appcompat.app.AppCompatActivity
import androidx.fragment.app.Fragment
import com.google.android.material.tabs.TabLayout
import com.vss.vehicleapp.databinding.ActivityMainBinding
{imports}

class MainActivity : AppCompatActivity() {{

    private lateinit var binding: ActivityMainBinding
    private val fragments = listOf({fragments_list})
    private val domains = listOf({', '.join(f'"{d.upper()}"' for d in domains)})

    override fun onCreate(savedInstanceState: Bundle?) {{
        super.onCreate(savedInstanceState)
        binding = ActivityMainBinding.inflate(layoutInflater)
        setContentView(binding.root)

        val tabLayout: TabLayout = binding.tabLayout
{tab_items}

        // Load first fragment
        loadFragment(fragments[0])

        tabLayout.addOnTabSelectedListener(object : TabLayout.OnTabSelectedListener {{
            override fun onTabSelected(tab: TabLayout.Tab) {{
                loadFragment(fragments[tab.position])
            }}
            override fun onTabUnselected(tab: TabLayout.Tab) {{}}
            override fun onTabReselected(tab: TabLayout.Tab) {{}}
        }})
    }}

    private fun loadFragment(fragment: Fragment) {{
        supportFragmentManager.beginTransaction()
            .replace(R.id.fragmentContainer, fragment)
            .commit()
    }}
}}
"""

# ── AndroidManifest ──────────────────────────────────────────────
def _generate_manifest() -> str:
    return """<?xml version="1.0" encoding="utf-8"?>
<manifest xmlns:android="http://schemas.android.com/apk/res/android"
    package="com.vss.vehicleapp">

    <uses-permission android:name="android.car.permission.CAR_SPEED"/>
    <uses-permission android:name="android.car.permission.CAR_ENERGY"/>
    <uses-permission android:name="android.car.permission.CAR_HVAC"/>
    <uses-permission android:name="android.car.permission.CAR_INFO"/>
    <uses-permission android:name="android.car.permission.CAR_POWERTRAIN"/>
    <uses-permission android:name="android.car.permission.CAR_EXTERIOR_LIGHTS"/>
    <uses-permission android:name="android.car.permission.CAR_INTERIOR_LIGHTS"/>
    <uses-permission android:name="android.car.permission.CAR_DYNAMICS_STATE"/>
    <uses-permission android:name="android.car.permission.CAR_DRIVING_STATE"/>
    <uses-permission android:name="android.car.permission.CONTROL_CAR_CLIMATE"/>
    <uses-permission android:name="android.car.permission.CONTROL_CAR_EXTERIOR_LIGHTS"/>
    <uses-permission android:name="android.car.permission.CONTROL_CAR_DOORS"/>
    <uses-permission android:name="android.car.permission.CONTROL_CAR_MIRRORS"/>
    <uses-permission android:name="android.car.permission.CONTROL_CAR_SEATS"/>
    <uses-permission android:name="android.car.permission.CAR_VENDOR_EXTENSION"/>

    <application
        android:label="VSS Vehicle Dashboard"
        android:theme="@style/Theme.MaterialComponents.DayNight">
        <activity
            android:name=".MainActivity"
            android:exported="true">
            <intent-filter>
                <action android:name="android.intent.action.MAIN"/>
                <category android:name="android.intent.category.LAUNCHER"/>
            </intent-filter>
        </activity>
    </application>

</manifest>
"""

# ── Android.bp ───────────────────────────────────────────────────
def _generate_android_bp() -> str:
    return """android_app {
    name: "VssDashboardApp",
    srcs: ["src/**/*.kt"],
    resource_dirs: ["res"],
    manifest: "AndroidManifest.xml",
    platform_apis: true,
    certificate: "platform",
    privileged: true,
    libs: [
        "android.car",
    ],
    static_libs: [
        "androidx.appcompat_appcompat",
        "com.google.android.material_material",
        "androidx.constraintlayout_constraintlayout",
        "androidx.fragment_fragment-ktx",
    ],
    optimize: {
        enabled: false,
    },
    dex_preopt: {
        enabled: false,
    },
    vendor: false,
}
"""

# ── Main ─────────────────────────────────────────────────────────
def load_from_c4_output(c4_dir: str, aosp_dump_dir: str) -> dict:
    """
    Dynamically load VSS properties from C4 YAML spec + compiled IDs from AOSP dump.
    Works with any signal count and any number of modules.

    Parameters
    ----------
    c4_dir       : path to output_c4_feedback/ directory
    aosp_dump_dir: path to aosp_dump/ directory containing VehicleProperty*.aidl files

    Returns
    -------
    dict: domain -> list of (name, int_id, type, access, description)
    """
    try:
        import yaml
    except ImportError:
        print("  [C5] pyyaml not installed — run: pip install pyyaml")
        return {}

    c4_path   = Path(c4_dir)
    dump_path = Path(aosp_dump_dir)

    # ── Step 1: Load C4 YAML spec (has name, type, access per property) ──
    spec_files = sorted(c4_path.glob("SPEC_FROM_VSS_*.yaml"))
    if not spec_files:
        print(f"  [C5] No YAML spec found in {c4_dir} — falling back to hardcoded map")
        return {}

    spec_file = spec_files[-1]  # use latest
    print(f"  [C5] Loading spec: {spec_file.name}")
    raw_spec = yaml.safe_load(spec_file.read_text())

    # Build lookup: property_name -> {type, access}
    prop_meta = {}
    for prop in raw_spec.get("properties", []):
        name   = prop.get("id", "") or prop.get("name", "")
        typ    = prop.get("type", "INT32").upper()
        access = prop.get("access", "READ").upper()
        if name:
            prop_meta[name] = {"type": typ, "access": access}
    print(f"  [C5] Spec loaded: {len(prop_meta)} properties")

    # ── Step 2: Load MODULE_PLAN.json (domain groupings) ──
    module_plan_path = c4_path / "MODULE_PLAN.json"
    if not module_plan_path.exists():
        # Try root output dir
        module_plan_path = Path("output") / "MODULE_PLAN.json"
    if not module_plan_path.exists():
        print(f"  [C5] MODULE_PLAN.json not found — falling back to hardcoded map")
        return {}

    module_plan = json.loads(module_plan_path.read_text())
    modules = module_plan.get("modules", [])
    print(f"  [C5] Module plan: {len(modules)} modules")

    # ── Step 3: Load compiled IDs from AOSP dump ──
    compiled_ids = {}  # prop_name -> int_id
    if dump_path.exists():
        for aidl_file in sorted(dump_path.glob("VehicleProperty*.aidl")):
            count = 0
            for line in aidl_file.read_text().splitlines():
                m = re.match(r'\s+(\w+)\s*=\s*(0x[0-9a-fA-F]+)', line)
                if m:
                    compiled_ids[m.group(1)] = int(m.group(2), 16)
                    count += 1
            print(f"  [C5]   {aidl_file.name}: {count} IDs loaded")
    else:
        print(f"  [C5] AOSP dump not found at {dump_path} — IDs will use domain base offset")

    # ── Step 4: Build domain map dynamically ──
    domain_map = {}
    for module in modules:
        domain     = module.get("domain", "unknown").lower()
        prop_names = module.get("properties", [])
        base       = DOMAIN_BASE.get(domain, 0x8000)

        props = []
        for idx, prop_name in enumerate(prop_names):
            # Get compiled ID from AOSP dump, fallback to domain base + index
            prop_id = compiled_ids.get(prop_name, base + idx)

            # Get type/access from YAML spec
            meta   = prop_meta.get(prop_name, {})
            typ    = meta.get("type", "INT32")
            access = meta.get("access", "READ")

            # Clean description from VSS path
            desc = prop_name.replace("VEHICLE_CHILDREN_", "")
            desc = desc.replace("_CHILDREN_", ".")
            desc = desc[:60].lower()

            props.append((prop_name, prop_id, typ, access, desc))

        if props:
            domain_map[domain] = props
            print(f"  [C5] {domain.upper():15s}: {len(props):4d} properties "
                  f"(IDs: {hex(props[0][1])}–{hex(props[-1][1])})")

    total = sum(len(v) for v in domain_map.values())
    print(f"  [C5] Total: {total} properties across {len(domain_map)} domains")
    return domain_map


def main():
    OUTPUT_DIR.mkdir(parents=True, exist_ok=True)

    print("=" * 70)
    print("  C5 — VSS HMI App Generation (Real AOSP CarPropertyManager IDs)")
    print("=" * 70)

    # ── Load property map dynamically or fall back to hardcoded ──
    c4_dir   = "output_c4_feedback"
    aosp_dir = "aosp_dump"

    if Path(c4_dir).exists() and (Path(aosp_dir).exists() or True):
        print("  Loading dynamically from C4 output + AOSP dump...")
        property_map = load_from_c4_output(c4_dir, aosp_dir)

    if not property_map:
        print("  Falling back to hardcoded Cuttlefish property map...")
        property_map = AOSP_PROPERTY_MAP

    print(f"  Output : {OUTPUT_DIR.resolve()}")
    print(f"  Domains: {list(property_map.keys())}")
    print(f"  Total  : {sum(len(v) for v in property_map.values())} properties")
    print()

    # Setup output directories
    src_dir    = OUTPUT_DIR / "src" / "main" / "java" / "com" / "vss" / "vehicleapp"
    frag_dir   = src_dir / "fragments"
    res_dir    = OUTPUT_DIR / "src" / "main" / "res" / "layout"
    for d in [src_dir, frag_dir, res_dir]:
        d.mkdir(parents=True, exist_ok=True)

    # Load DSPy program
    prog = _load_dspy_app_program()

    domains = list(property_map.keys())

    # Generate per-domain fragments + layouts
    for domain in domains:
        properties = property_map[domain]
        print(f"  [{domain.upper()}] Generating fragment ({len(properties)} properties)...")

        t0 = time.time()
        fragment_code = _generate_fragment(domain, properties, prog)
        layout_xml    = _generate_layout(domain, properties)

        frag_file   = frag_dir / f"{domain.capitalize()}Fragment.kt"
        layout_file = res_dir  / f"fragment_{domain}.xml"

        frag_file.write_text(fragment_code)
        layout_file.write_text(layout_xml)

        print(f"    ✓ {frag_file.name} ({len(fragment_code)} chars, {time.time()-t0:.1f}s)")
        print(f"    ✓ {layout_file.name} ({len(layout_xml)} chars)")

    # Generate MainActivity
    print(f"\n  Generating MainActivity...")
    main_code = _generate_main_activity(domains)
    (src_dir / "MainActivity.kt").write_text(main_code)
    print(f"    ✓ MainActivity.kt")

    # Generate static files
    print(f"  Generating static files...")
    (OUTPUT_DIR / "AndroidManifest.xml").write_text(_generate_manifest())
    (OUTPUT_DIR / "Android.bp").write_text(_generate_android_bp())
    print(f"    ✓ AndroidManifest.xml")
    print(f"    ✓ Android.bp")

    # Summary
    kt_files  = list(frag_dir.glob("*.kt")) + [src_dir / "MainActivity.kt"]
    xml_files = list(res_dir.glob("*.xml"))

    print(f"\n{'=' * 70}")
    print(f"  C5 Generation Complete!")
    print(f"{'=' * 70}")
    print(f"  Kotlin fragments : {len(kt_files)}")
    print(f"  XML layouts      : {len(xml_files)}")
    print(f"  Output           : {OUTPUT_DIR.resolve()}")
    print()
    print("  Next steps:")
    print("  1. Copy output_c5_hmi/ to Android Studio as a new Automotive project")
    print("  2. Build APK: ./gradlew assembleDebug")
    print(f"  3. Install: adb -s 0.0.0.0:6520 install app-debug.apk")
    print("  4. Verify on Cuttlefish: properties update in real-time via CarPropertyManager")
    print(f"{'=' * 70}")


if __name__ == "__main__":
    main()
