# FILE: agents/llm_backend_agent_adaptive.py
"""
Backend Agent - ADAPTIVE VERSION
Wraps your existing llm_backend_agent with adaptive intelligence
"""
import sys
from pathlib import Path

# Import your original agent
sys.path.insert(0, str(Path(__file__).parent.parent))
from agents.llm_backend_agent import (
    LLMBackendAgent as OriginalBackendAgent
)

# Import adaptive wrapper
from adaptive_integration import get_adaptive_wrapper


class LLMBackendAgentAdaptive(OriginalBackendAgent):
    """
    Adaptive version of your Backend Agent
    """
    
    def __init__(self, output_dir: str = "output"):
        super().__init__(output_dir)
        self.adaptive_wrapper = get_adaptive_wrapper()
        
        print(f"✓ Adaptive Backend Agent initialized")
    
    def generate(
        self,
        spec: dict,
        modules: list,
        llm_first: bool = True,
        timeout_per_file: int = 60
    ) -> dict:
        """
        Override generate with adaptive intelligence
        """
        print(f"\n[ADAPTIVE BACKEND] Starting generation...")
        
        # Extract properties
        all_properties = []
        for module in modules:
            if 'properties' in module:
                all_properties.extend(module['properties'])
        
        # Wrapper function
        def original_generation(**kwargs):
            return super(LLMBackendAgentAdaptive, self).generate(
                spec=spec,
                modules=modules,
                llm_first=llm_first,
                timeout_per_file=timeout_per_file
            )
        
        # Wrap with adaptive
        result, metadata = self.adaptive_wrapper.wrap_generation(
            agent_name="BackendAgent",
            properties=all_properties,
            original_generation_func=original_generation
        )
        
        if isinstance(result, dict):
            result['adaptive_metadata'] = metadata
        
        return result


def generate_backend_llm_first(spec: dict, modules: list) -> dict:
    """Adaptive version - drop-in replacement"""
    agent = LLMBackendAgentAdaptive()
    return agent.generate(spec, modules, llm_first=True)