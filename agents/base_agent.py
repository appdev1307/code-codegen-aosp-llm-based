import os
from typing import Dict, Optional
from llm_client import call_llm


class BaseAgent:
    """
    BaseAgent for AAOS code generation.
    - Hỗ trợ retry thông minh (error_context)
    - Spec dạng dict (JSON-like)
    - Không in content ra console
    """

    def __init__(
        self,
        name: str,
        system_prompt: str,
        output_file: str,
        output_dir: str = "output",
    ):
        self.name = name
        self.system_prompt = system_prompt.strip()
        self.output_file = output_file
        self.output_dir = output_dir

    def build_prompt(
        self,
        spec: Dict,
        error_context: Optional[str] = None,
    ) -> str:
        prompt = f"""
{self.system_prompt}

TARGET:
- Android Automotive OS (AAOS)
- HIDL-based Vehicle HAL
- Must follow AOSP conventions

SPECIFICATION (JSON):
{spec}
"""

        if error_context:
            prompt += f"""

PREVIOUS VALIDATION ERRORS:
{error_context}

MANDATORY:
- Fix ALL errors above
- Do NOT repeat previous mistakes
- Output MUST pass validator
"""

        prompt += """

OUTPUT RULES:
- Output ONLY the file content
- NO explanation
- NO markdown
"""

        return prompt.strip()

    def run(
        self,
        spec: Dict,
        error_context: Optional[str] = None,
    ) -> str:
        print(f"[DEBUG] {self.name}: start", flush=True)

        prompt = self.build_prompt(spec, error_context)
        result = call_llm(prompt)

        os.makedirs(self.output_dir, exist_ok=True)
        path = os.path.join(self.output_dir, self.output_file)

        with open(path, "w") as f:
            f.write(result)

        print(f"[DEBUG] {self.name}: output -> {path}", flush=True)
        print(f"[DEBUG] {self.name}: done", flush=True)

        return result
