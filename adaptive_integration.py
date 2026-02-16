# FILE: adaptive_integration.py (UPDATED WITH YOUR MODEL)

"""
Adaptive Integration Layer - Configured for YOUR setup
Uses qwen2.5-coder from your llm_client.py
"""

import sys
import os
import time
from pathlib import Path
from typing import Dict, List, Optional, Tuple, Any

# Add adaptive components to path
sys.path.insert(0, str(Path(__file__).parent / 'adaptive_components'))

from adaptive_components.performance_tracker import PerformanceTracker, GenerationRecord
from adaptive_components.chunk_size_optimizer import ThompsonSamplingOptimizer
from adaptive_components.prompt_selector import PromptSelector

# Import YOUR llm_client to get the actual model name
try:
    from llm_client import MODEL as LLM_MODEL
    print(f"[ADAPTIVE] Detected LLM model from llm_client.py: {LLM_MODEL}")
except ImportError:
    LLM_MODEL = "qwen2.5-coder:32b"  # Fallback
    print(f"[ADAPTIVE] Using fallback model: {LLM_MODEL}")


class AdaptiveGenerationWrapper:
    """
    Wraps your existing LLM generation with adaptive intelligence
    Works with your llm_client.call_llm() calls
    """
    
    def __init__(
        self,
        enable_adaptive_chunking: bool = True,
        enable_adaptive_prompts: bool = True,
        output_dir: str = "adaptive_outputs"
    ):
        """
        Initialize adaptive wrapper
        
        Args:
            enable_adaptive_chunking: Use RL for chunk size optimization
            enable_adaptive_prompts: Use adaptive prompt selection
            output_dir: Where to store adaptive state
        """
        self.output_dir = Path(output_dir)
        self.output_dir.mkdir(exist_ok=True)
        
        # Store the detected model
        self.llm_model = LLM_MODEL
        
        # Initialize adaptive components
        self.tracker = PerformanceTracker(
            db_path=str(self.output_dir / "performance_history.db")
        )
        
        self.chunk_optimizer = ThompsonSamplingOptimizer() if enable_adaptive_chunking else None
        self.prompt_selector = PromptSelector() if enable_adaptive_prompts else None
        
        # Load previous state if exists
        self._load_state()
        
        self.enable_chunking = enable_adaptive_chunking
        self.enable_prompts = enable_adaptive_prompts
        
        print(f"✓ Adaptive Generation Wrapper initialized")
        print(f"  LLM Model: {self.llm_model}")
        print(f"  Adaptive chunking: {enable_adaptive_chunking}")
        print(f"  Adaptive prompts: {enable_adaptive_prompts}")
        print(f"  Output directory: {output_dir}")
    
    def _load_state(self):
        """Load previous learning state"""
        chunk_state = self.output_dir / "chunk_optimizer_state.json"
        prompt_state = self.output_dir / "prompt_selector_state.json"
        
        if self.chunk_optimizer and chunk_state.exists():
            try:
                self.chunk_optimizer.load_state(str(chunk_state))
                print(f"  ✓ Loaded chunk optimizer state")
            except Exception as e:
                print(f"  ⚠ Could not load chunk state: {e}")
        
        if self.prompt_selector and prompt_state.exists():
            try:
                self.prompt_selector.load_state(str(prompt_state))
                print(f"  ✓ Loaded prompt selector state")
            except Exception as e:
                print(f"  ⚠ Could not load prompt state: {e}")
    
    def _save_state(self):
        """Save learning state for persistence"""
        try:
            if self.chunk_optimizer:
                self.chunk_optimizer.save_state(
                    str(self.output_dir / "chunk_optimizer_state.json")
                )
            
            if self.prompt_selector:
                self.prompt_selector.save_state(
                    str(self.output_dir / "prompt_selector_state.json")
                )
        except Exception as e:
            print(f"  ⚠ Could not save state: {e}")
    
    def decide_generation_strategy(
        self,
        properties: List[Dict],
        agent_name: str
    ) -> Dict:
        """
        Decide: full generation vs progressive chunking
        
        Returns:
            {
                'strategy': 'full' | 'progressive',
                'chunk_size': int,
                'chunks': List[List[Dict]],
                'timeout': float,
                'prompt_variant': str
            }
        """
        property_count = len(properties) if properties else 0
        
        # Decide chunk size (adaptive or static)
        if self.chunk_optimizer and property_count > 30:
            chunk_size = self.chunk_optimizer.select_chunk_size(property_count)
            strategy = 'progressive'
        elif property_count <= 30:
            chunk_size = property_count if property_count > 0 else 10
            strategy = 'full'
        else:
            # Fallback to static heuristic
            chunk_size = 20
            strategy = 'progressive'
        
        # Create chunks
        if strategy == 'full' or property_count == 0:
            chunks = [properties] if properties else [[]]
        else:
            chunks = self._create_chunks(properties, chunk_size)
        
        # Calculate timeout (adaptive based on chunk size)
        # NOTE: Your llm_client.py has DEFAULT_TIMEOUT=1200 (20 min)
        # We'll use proportional timeouts here
        base_timeout = 600.0  # 10 minutes base
        if chunks and len(chunks[0]) > 0:
            timeout = base_timeout * (1.0 + len(chunks[0]) / 30.0)
        else:
            timeout = base_timeout
        
        # Select prompt variant (adaptive or default)
        if self.prompt_selector and property_count > 0:
            prompt_variant = self.prompt_selector.select_variant(property_count)
        else:
            prompt_variant = 'detailed'  # Static default
        
        decision = {
            'strategy': strategy,
            'chunk_size': chunk_size,
            'chunks': chunks,
            'timeout': timeout,
            'prompt_variant': prompt_variant,
            'property_count': property_count
        }
        
        print(f"\n[ADAPTIVE DECISION] Agent: {agent_name}")
        print(f"  Properties: {property_count}")
        print(f"  Strategy: {strategy}")
        print(f"  Chunk size: {chunk_size}")
        print(f"  Num chunks: {len(chunks)}")
        print(f"  Timeout: {timeout:.1f}s")
        print(f"  Prompt variant: {prompt_variant}")
        print(f"  Model: {self.llm_model}")
        
        return decision
    
    def _create_chunks(
        self,
        properties: List[Dict],
        chunk_size: int
    ) -> List[List[Dict]]:
        """Split properties into chunks"""
        if not properties:
            return [[]]
        
        chunks = []
        for i in range(0, len(properties), chunk_size):
            chunks.append(properties[i:i + chunk_size])
        return chunks
    
    def enhance_prompt(
        self,
        base_prompt: str,
        properties: List[Dict],
        prompt_variant: str
    ) -> str:
        """
        Enhance base prompt with adaptive variant
        
        This works with your existing prompts by adding context
        """
        if not self.prompt_selector:
            return base_prompt
        
        # Get variant-specific enhancements
        variant_enhancements = {
            'minimal': '\n\nSTYLE: Generate minimal, concise code. Focus on essentials only.',
            'detailed': '\n\nSTYLE: Generate comprehensive code with detailed comments and error handling.',
            'conservative': '\n\nSTYLE: Use safe, proven patterns. Prioritize correctness over cleverness.',
            'aggressive': '\n\nSTYLE: Use modern patterns and optimizations. Advanced features welcome.'
        }
        
        enhancement = variant_enhancements.get(prompt_variant, '')
        
        return base_prompt + enhancement
    
    def wrap_generation(
        self,
        agent_name: str,
        properties: List[Dict],
        original_generation_func,
        **kwargs
    ) -> Tuple[Any, Dict]:
        """
        Wrap a generation function with adaptive intelligence
        
        Args:
            agent_name: Name of agent (e.g., "AndroidAppAgent")
            properties: Properties to generate for
            original_generation_func: Function that does actual generation
            **kwargs: Additional arguments
        
        Returns:
            (result, metadata) - Result from generation + adaptive metadata
        """
        start_time = time.time()
        
        # Make adaptive decision
        decision = self.decide_generation_strategy(properties, agent_name)
        
        # Add adaptive decisions to kwargs
        adaptive_kwargs = kwargs.copy()
        adaptive_kwargs['adaptive_decision'] = decision
        adaptive_kwargs['chunk_size'] = decision['chunk_size']
        adaptive_kwargs['timeout'] = decision['timeout']
        adaptive_kwargs['prompt_variant'] = decision['prompt_variant']
        
        # Generate code (using original function)
        success = False
        quality_score = 0.0
        error_type = None
        error_message = None
        result = None
        
        try:
            result = original_generation_func(**adaptive_kwargs)
            
            # Evaluate success
            success = self._evaluate_success(result)
            quality_score = self._evaluate_quality(result)
            
        except Exception as e:
            error_type = type(e).__name__
            error_message = str(e)
            print(f"  ✗ Generation failed: {error_type}: {error_message}")
        
        generation_time = time.time() - start_time
        
        # Record performance for learning
        record = GenerationRecord(
            timestamp=start_time,
            module_name=agent_name,
            property_count=decision['property_count'],
            chunk_size=decision['chunk_size'],
            timeout=decision['timeout'],
            prompt_variant=decision['prompt_variant'],
            success=success,
            quality_score=quality_score,
            generation_time=generation_time,
            error_type=error_type,
            error_message=error_message,
            llm_model=self.llm_model  # Use detected model
        )
        
        self.tracker.record_generation(record)
        
        # Update adaptive components (learning)
        if self.chunk_optimizer and decision['property_count'] > 0:
            self.chunk_optimizer.update_reward(
                chunk_size=decision['chunk_size'],
                success=success,
                quality_score=quality_score,
                generation_time=generation_time
            )
        
        if self.prompt_selector and decision['property_count'] > 0:
            self.prompt_selector.update_performance(
                variant=decision['prompt_variant'],
                property_count=decision['property_count'],
                success=success,
                quality_score=quality_score,
                generation_time=generation_time
            )
        
        # Save state periodically
        self._save_state()
        
        # Prepare metadata
        metadata = {
            'adaptive_decision': decision,
            'success': success,
            'quality_score': quality_score,
            'generation_time': generation_time,
            'learning_stats': self._get_learning_stats()
        }
        
        if success:
            print(f"  ✓ Generation successful (quality: {quality_score:.2f}, time: {generation_time:.1f}s)")
        else:
            print(f"  ✗ Generation failed (time: {generation_time:.1f}s)")
        
        return result, metadata
    
    def _evaluate_success(self, result) -> bool:
        """
        Evaluate if generation was successful
        
        Checks if result looks valid
        """
        if result is None:
            return False
        
        # Check if it's a string with content
        if isinstance(result, str):
            return len(result.strip()) > 100
        
        # Check if it's a dict with success indicator
        if isinstance(result, dict):
            return result.get('success', False) or bool(result.get('content'))
        
        # Default: assume success if not None
        return True
    
    def _evaluate_quality(self, result) -> float:
        """
        Evaluate generated code quality
        
        Returns score 0.0 to 1.0
        """
        if result is None:
            return 0.0
        
        score = 0.5  # Base score
        
        # If string result, check length
        if isinstance(result, str):
            if len(result) > 100:
                score += 0.2
            if len(result) > 500:
                score += 0.1
            # Check for common quality indicators
            if 'import' in result or 'package' in result:
                score += 0.1
            if '{' in result and '}' in result:
                score += 0.1
        
        # If dict result
        elif isinstance(result, dict):
            if result.get('success'):
                score += 0.3
            if result.get('files'):
                score += 0.2
        
        return min(score, 1.0)
    
    def _get_learning_stats(self) -> Dict:
        """Get current learning statistics"""
        stats = {
            'total_generations': self.tracker.get_statistics()['total_generations']
        }
        
        if self.chunk_optimizer:
            chunk_stats = self.chunk_optimizer.get_statistics()
            stats['best_chunk_size'] = chunk_stats['best_chunk_size']
            stats['chunk_expected_rewards'] = chunk_stats['expected_rewards']
        
        if self.prompt_selector:
            prompt_stats = self.prompt_selector.get_statistics()
            stats['prompt_performance'] = prompt_stats.get('overall_performance', {})
        
        return stats
    
    def get_full_statistics(self) -> Dict:
        """Get comprehensive statistics for analysis"""
        return {
            'tracker': self.tracker.get_statistics(),
            'chunk_optimizer': self.chunk_optimizer.get_statistics() if self.chunk_optimizer else None,
            'prompt_selector': self.prompt_selector.get_statistics() if self.prompt_selector else None
        }
    
    def export_results(self, output_path: str = None):
        """Export all results for thesis analysis"""
        if output_path is None:
            output_path = str(self.output_dir / "full_results.json")
        
        self.tracker.export_to_json(output_path)
        print(f"✓ Results exported to {output_path}")


# Global adaptive wrapper instance (singleton pattern)
_global_wrapper = None


def get_adaptive_wrapper(
    enable_all: bool = True,
    output_dir: str = "adaptive_outputs"
) -> AdaptiveGenerationWrapper:
    """
    Get or create global adaptive wrapper
    
    Usage in your agents:
        from adaptive_integration import get_adaptive_wrapper
        wrapper = get_adaptive_wrapper()
    """
    global _global_wrapper
    
    if _global_wrapper is None:
        _global_wrapper = AdaptiveGenerationWrapper(
            enable_adaptive_chunking=enable_all,
            enable_adaptive_prompts=enable_all,
            output_dir=output_dir
        )
    
    return _global_wrapper


if __name__ == "__main__":
    # Test the wrapper
    print("Testing Adaptive Integration Wrapper...\n")
    
    # Mock generation function
    def mock_generate(**kwargs):
        print(f"  Mock generation called")
        print(f"  Chunk size: {kwargs.get('chunk_size')}")
        print(f"  Timeout: {kwargs.get('timeout')}")
        print(f"  Variant: {kwargs.get('prompt_variant')}")
        time.sleep(0.5)  # Simulate generation
        return "Generated code here..."
    
    # Create wrapper
    wrapper = get_adaptive_wrapper()
    
    # Test generation
    test_properties = [{'name': f'prop_{i}', 'type': 'INT'} for i in range(50)]
    result, metadata = wrapper.wrap_generation(
        agent_name="TestAgent",
        properties=test_properties,
        original_generation_func=mock_generate
    )
    
    print("\n✓ Wrapper test complete!")
    print(f"Model used: {wrapper.llm_model}")
    print(f"Result: {result[:50]}...")
    print(f"Success: {metadata['success']}")
    print(f"Quality: {metadata['quality_score']:.2f}")