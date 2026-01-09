from llm_client import call_llm

def generate_vhal_aidl(spec):
    print("[AGENT:AIDL] Generating Vehicle HAL AIDL")

    prompt = f"""
Generate AAOS Vehicle HAL AIDL for domain {spec.domain}.

Requirements:
- AIDL-based Vehicle HAL (Android {spec.aosp_level}+)
- Package: android.hardware.automotive.vehicle
- IVehicle with get/set
- VehiclePropValue parcelable
- Support properties:
{[p.id for p in spec.properties]}

Output REAL AOSP-COMPATIBLE AIDL files.
"""

    return call_llm(prompt)
