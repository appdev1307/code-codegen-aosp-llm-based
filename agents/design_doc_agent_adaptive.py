# FILE: agents/design_doc_agent_adaptive.py
"""
Design Doc Agent - ADAPTIVE VERSION
Wraps your existing design_doc_agent with adaptive intelligence
"""
import sys
from pathlib import Path

# Import your original agent
sys.path.insert(0, str(Path(__file__).parent.parent))
from agents.design_doc_agent import (
    DesignDocAgent as OriginalDesignDocAgent
)

# Import adaptive wrapper
from adaptive_integration import get_adaptive_wrapper


class DesignDocAgentAdaptive(OriginalDesignDocAgent):
    """
    Adaptive version of your Design Doc Agent
    """
    
    def __init__(self, output_dir: str = "output"):
        super().__init__(output_dir)
        self.adaptive_wrapper = get_adaptive_wrapper()
        
        print(f"✓ Adaptive Design Doc Agent initialized")
    
    def generate(
        self,
        spec: dict,
        modules: list,
        timeout_per_diagram: int = 180
    ) -> dict:
        """
        Override generate with adaptive intelligence
        """
        print(f"\n[ADAPTIVE DESIGN DOC] Starting generation...")
        
        # Extract properties
        all_properties = []
        for module in modules:
            if 'properties' in module:
                all_properties.extend(module['properties'])
        
        # Wrapper function
        def original_generation(**kwargs):
            return super(DesignDocAgentAdaptive, self).generate(
                spec=spec,
                modules=modules,
                timeout_per_diagram=timeout_per_diagram
            )
        
        # Wrap with adaptive
        result, metadata = self.adaptive_wrapper.wrap_generation(
            agent_name="DesignDocAgent",
            properties=all_properties,
            original_generation_func=original_generation
        )
        
        if isinstance(result, dict):
            result['adaptive_metadata'] = metadata
        
        return result


def generate_design_doc(spec: dict, modules: list) -> dict:
    """Adaptive version - drop-in replacement"""
    agent = DesignDocAgentAdaptive()
    return agent.generate(spec, modules)