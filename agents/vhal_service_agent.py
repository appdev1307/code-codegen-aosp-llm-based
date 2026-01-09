from llm_client import call_llm

def generate_vhal_service(spec):
    print("[AGENT:VHAL] Generating native Vehicle HAL service")

    prompt = f"""
Generate native C++ Vehicle HAL service for AAOS.

Domain: {spec.domain}
Properties:
{[(p.id, p.type, p.access) for p in spec.properties]}

Requirements:
- Inherit from BnIVehicle
- Implement get/set
- Use VehiclePropValue
- Thread safe
- Notify callbacks
"""

    return call_llm(prompt)
