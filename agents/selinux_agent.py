from agents.base_agent import BaseAgent

SYSTEM_PROMPT = """
You are an Android Automotive SELinux expert.
Generate minimal, correct SELinux policy for CarService and VHAL.
Follow AOSP SELinux best practices.
Avoid overly permissive rules.
Output only SELinux .te policy.
MANDATORY SELINUX RULES:
- MUST define: type car_hvac_service, domain;
- MUST include type declaration before allow rules
- Output ONLY valid .te syntax
- FAIL if type is missing
"""

_agent = BaseAgent(
    name="SELinux Agent",
    system_prompt=SYSTEM_PROMPT,
    output_file="car_hvac_service.te",
)

def generate_selinux(spec: str):
    return _agent.run(spec)
