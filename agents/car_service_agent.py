from agents.base_agent import BaseAgent

SYSTEM_PROMPT = """
You are an Android Automotive CarService framework engineer.
Generate Java code integrated with CarService.
Ensure permission enforcement and CTS compliance.
MANDATORY:
- MUST use android.car.hardware.property.CarPropertyManager
- MUST call getCarPropertyManager()
- MUST enforce permission via enforceCallingPermission()
- DO NOT use direct HAL access
"""

_agent = BaseAgent(
    name="CarService Agent",
    system_prompt=SYSTEM_PROMPT,
    output_file="CarService.java"
)

def generate_car_service(spec: str):
    return _agent.run(spec)
