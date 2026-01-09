from llm_client import call_llm

def generate_car_service(spec):
    print("[AGENT:CARSVC] Generating CarService integration")

    prompt = f"""
Generate Android Framework CarService code.

Domain: {spec.domain}
Properties: {[p.id for p in spec.properties]}

Requirements:
- Use CarPropertyManager
- Framework-side listener
"""

    return call_llm(prompt)
