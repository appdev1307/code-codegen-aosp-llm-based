from llm_client import call_llm

def generate_selinux(spec):
    print("[AGENT:SELINUX] Generating SELinux policy")

    prompt = f"""
Generate SELinux policy for AAOS HAL.

Domain: {spec.domain}
Vendor: {spec.vendor}

Requirements:
- HAL service domain
- Binder access
"""

    return call_llm(prompt)
