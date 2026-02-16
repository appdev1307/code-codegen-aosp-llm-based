# FILE: agents/llm_android_app_agent_adaptive.py
"""
Android App Agent - ADAPTIVE VERSION
Wraps your existing llm_android_app_agent with adaptive intelligence
"""
import sys
from pathlib import Path

# Import your original agent
sys.path.insert(0, str(Path(__file__).parent.parent))
from agents.llm_android_app_agent import (
    LLMAndroidAppAgent as OriginalAndroidAppAgent
)

# Import adaptive wrapper
from adaptive_integration import get_adaptive_wrapper


class LLMAndroidAppAgentAdaptive(OriginalAndroidAppAgent):
    """
    Adaptive version of your Android App Agent
    
    Inherits all functionality from your original agent,
    adds adaptive intelligence on top
    """
    
    def __init__(self, output_dir: str = "output"):
        # Initialize parent (your original agent)
        super().__init__(output_dir)
        
        # Get global adaptive wrapper
        self.adaptive_wrapper = get_adaptive_wrapper()
        
        print(f"✓ Adaptive Android App Agent initialized")
        print(f"  Base agent: LLMAndroidAppAgent")
        print(f"  Adaptive features: ENABLED")
    
    def generate(
        self,
        spec: dict,
        modules: list,
        llm_first: bool = True,
        timeout_per_file: int = 60
    ) -> dict:
        """
        Override generate method with adaptive intelligence
        
        This wraps your original generate() call
        """
        print(f"\n[ADAPTIVE ANDROID APP] Starting generation...")
        
        # Extract properties from modules for adaptive decision
        all_properties = []
        for module in modules:
            if 'properties' in module:
                all_properties.extend(module['properties'])
        
        # Create wrapper around original generation
        def original_generation(**kwargs):
            # Call your original generate method
            return super(LLMAndroidAppAgentAdaptive, self).generate(
                spec=spec,
                modules=modules,
                llm_first=llm_first,
                timeout_per_file=timeout_per_file
            )
        
        # Wrap with adaptive intelligence
        result, metadata = self.adaptive_wrapper.wrap_generation(
            agent_name="AndroidAppAgent",
            properties=all_properties,
            original_generation_func=original_generation,
            llm_model="qwen2.5-coder:7b"
        )
        
        # Add metadata to result
        if isinstance(result, dict):
            result['adaptive_metadata'] = metadata
        
        return result
    
    def get_adaptive_statistics(self) -> dict:
        """Get learning statistics"""
        return self.adaptive_wrapper.get_full_statistics()


# For compatibility: keep original function name
def generate_android_app_llm_first(spec: dict, modules: list) -> dict:
    """
    Adaptive version of generate_android_app_llm_first
    Drop-in replacement for your original function
    """
    agent = LLMAndroidAppAgentAdaptive()
    return agent.generate(spec, modules, llm_first=True)