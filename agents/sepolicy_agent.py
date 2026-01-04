from agents.base_agent import BaseAgent

SYSTEM_PROMPT = """
You are an Android Automotive SELinux policy expert.
Generate correct AOSP-compliant sepolicy (.te) rules.
Follow least-privilege principle.
Avoid wildcard or overly permissive rules.
"""

_agent = BaseAgent(
    name="SEPolicy Agent",
    system_prompt=SYSTEM_PROMPT,
    output_file="vehicle_sepolicy.te"
)

def generate_sepolicy(spec: str):
    return _agent.run(spec)
