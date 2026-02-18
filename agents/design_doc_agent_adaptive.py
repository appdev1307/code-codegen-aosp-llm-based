# FILE: agents/design_doc_agent_adaptive.py
"""
Design Doc Agent - ADAPTIVE VERSION (Simple Wrapper)

What this does:
- Inherits from original DesignDocAgent
- Overrides run() to add RL recording
- Everything else identical to original
"""

import time
from pathlib import Path

# Import original agent
from agents.design_doc_agent import DesignDocAgent as OriginalDesignDocAgent

# Import adaptive wrapper
from adaptive_integration import get_adaptive_wrapper


class DesignDocAgentAdaptive(OriginalDesignDocAgent):
    """Adaptive wrapper around original DesignDocAgent"""
    
    def __init__(self, output_root: str = "output"):
        super().__init__(output_root)
        self.adaptive = get_adaptive_wrapper()
    
    def run(self, module_signal_map: dict, all_properties: list, full_spec_text: str):
        """Run with RL recording"""
        prop_count = len(all_properties)
        
        decision = self.adaptive.decide_generation_strategy(
            properties=[{"name": f"p{i}"} for i in range(prop_count)],
            agent_name="DesignDocAgent"
        )
        
        print(f"[DESIGN DOC] Adaptive generation (variant={decision['prompt_variant']})")
        
        start_time = time.time()
        success = False
        
        try:
            super().run(module_signal_map, all_properties, full_spec_text)
            success = True
            quality = self._compute_quality()
        except Exception as e:
            print(f"  [ADAPTIVE] Failed: {e}")
            quality = 0.0
        
        elapsed = time.time() - start_time
        
        # Record for RL
        from adaptive_components.performance_tracker import GenerationRecord
        import time as _time
        
        self.adaptive.tracker.record_generation(GenerationRecord(
            timestamp=_time.time(),
            module_name="DesignDocAgent",
            property_count=prop_count,
            chunk_size=decision["chunk_size"],
            timeout=decision["timeout"],
            prompt_variant=decision["prompt_variant"],
            success=success,
            quality_score=quality,
            generation_time=elapsed,
            error_type=None,
            error_message=None,
            llm_model=self.adaptive.llm_model
        ))
        
        self.adaptive.chunk_optimizer.update_reward(
            chunk_size=decision["chunk_size"],
            success=success,
            quality_score=quality,
            generation_time=elapsed
        )
        self.adaptive.prompt_selector.update_performance(
            variant=decision["prompt_variant"],
            property_count=prop_count,
            success=success,
            quality_score=quality,
            generation_time=elapsed
        )
        self.adaptive._save_state()
    
    def _compute_quality(self) -> float:
        if not hasattr(self, 'stats'):
            return 0.5
        total = self.stats.get("total", 0)
        if total == 0:
            return 0.5
        llm = self.stats.get("llm_success", 0)
        return llm / total