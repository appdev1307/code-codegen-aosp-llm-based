from llm_client import call_llm
from tools.safe_writer import SafeWriter


class SelinuxAgent:
    def __init__(self, output_root: str = "output"):
        self.name = "SELinux Agent"
        self.output_dir = f"{output_root}/sepolicy"
        self.writer = SafeWriter(self.output_dir)

    @staticmethod
    def _trim_spec(spec_text: str) -> str:
        """Return a compact summary: domain + property names only.
        This avoids sending the full 50-property spec (large prompt -> timeout).
        """
        lines = []
        domain = "UNKNOWN"
        prop_names = []
        for line in spec_text.splitlines():
            stripped = line.strip()
            if stripped.startswith("HAL Domain:"):
                domain = stripped.split(":", 1)[1].strip()
            elif stripped.startswith("- Name:"):
                prop_names.append(stripped.split(":", 1)[1].strip())
        lines.append(f"Domain: {domain}")
        lines.append(f"Properties ({len(prop_names)}):")
        for n in prop_names:
            lines.append(f"  {n}")
        return "\n".join(lines)

    def build_prompt(self, spec_text: str) -> str:
        compact = self._trim_spec(spec_text)
        # Extract domain name for concrete example generation
        domain_lower = "unknown"
        for line in compact.splitlines():
            if line.strip().startswith("Domain:"):
                domain_lower = line.split(":", 1)[1].strip().lower()
                break

        return f"""
You are an Android SELinux policy expert.

Generate SELinux rules for an Android Automotive Vehicle HAL service.

ARCHITECTURE: all VSS domains run inside ONE shared process/domain called
hal_vehicle_vss (already declared and initialised elsewhere — do NOT
redeclare it, do NOT create a new domain for this VSS property group).
Every domain's C++ implementation performs real file I/O against
/data/vendor/vss_hw/{domain_lower}/ instead of stubbing property access,
so this fragment MUST grant access to the shared vss_hw_data_file type.

REQUIRED CONTENT — ALL of the following MUST appear in the output:
  allow hal_vehicle_vss vss_hw_data_file:dir {{ search add_name write create }};
  allow hal_vehicle_vss vss_hw_data_file:file {{ create read write open getattr unlink }};

Rules:
- This is AIDL (Android 14), NOT HIDL — never use hwservice_manager,
  add_hwservice, hwservice_use, hal_attribute, or hal_*_hwservice
- Do NOT declare: type <x>, domain; type <x>_exec, exec_type, ...;
  init_daemon_domain(...); hal_server_domain(...); binder_use(...);
  binder_call(...) — these are already granted to hal_vehicle_vss elsewhere
- Every allow rule's subject MUST be hal_vehicle_vss, never a per-domain name
- No placeholders
- No explanations

IMPORTANT:
- ALL file paths MUST be RELATIVE
- Use AOSP-style paths only
  (e.g. sepolicy/vendor/hal_vehicle_{domain_lower}.te)

Output format EXACTLY:
--- FILE: sepolicy/vendor/hal_vehicle_{domain_lower}.te ---
<file content>

Specification:
{compact}
"""

    def run(self, spec_text: str):
        print(f"[DEBUG] {self.name}: start", flush=True)

        result = call_llm(self.build_prompt(spec_text))
        if not result.strip():
            raise RuntimeError("[LLM ERROR] Empty SELinux output")

        self._write_files(result)

        print(f"[DEBUG] {self.name}: done", flush=True)
        return result

    def _write_files(self, text: str):
        current = None
        buf = []

        for line in text.splitlines():
            if line.strip().startswith("--- FILE:"):
                if current:
                    self.writer.write(current, "\n".join(buf))
                current = (
                    line.replace("--- FILE:", "")
                    .replace("---", "")
                    .strip()
                )
                buf = []
            else:
                buf.append(line)

        if current:
            self.writer.write(current, "\n".join(buf))


def generate_selinux(spec, output_root: str = "output"):
    return SelinuxAgent(output_root=output_root).run(spec.to_llm_spec())
