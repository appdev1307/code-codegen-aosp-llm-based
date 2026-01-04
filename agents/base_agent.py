import os
from llm_client import call_llm


class BaseAgent:
    def __init__(
        self,
        name: str,
        system_prompt: str,
        output_file: str,
        output_dir: str = "output",
    ):
        self.name = name
        self.system_prompt = system_prompt
        self.output_file = output_file
        self.output_dir = output_dir

    def build_prompt(self, spec: str) -> str:
        return f"""
{self.system_prompt}

Specification:
{spec}
"""

    def run(self, spec: str) -> str:
        print(f"[DEBUG] {self.name}: start", flush=True)

        prompt = self.build_prompt(spec)
        result = call_llm(prompt)

        os.makedirs(self.output_dir, exist_ok=True)
        path = os.path.join(self.output_dir, self.output_file)

        with open(path, "w") as f:
            f.write(result)

        print(f"[DEBUG] {self.name}: output -> {path}", flush=True)
        print(f"[DEBUG] {self.name}: done", flush=True)

        return result
