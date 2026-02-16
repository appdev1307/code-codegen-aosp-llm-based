# FILE: adaptive_components/prompt_selector.py
"""
Adaptive Prompt Selector - Learns which prompt variants work best
Complete, production-ready implementation
"""
import json
from typing import Dict, List, Optional
from collections import defaultdict
import numpy as np


class PromptSelector:
    """
    Maintains multiple prompt variants and selects best based on history
    """
    
    def __init__(self):
        self.prompt_variants = {
            'minimal': {
                'name': 'Minimal',
                'description': 'Concise, minimal code generation',
                'template': self._get_minimal_template()
            },
            'detailed': {
                'name': 'Detailed',
                'description': 'Comprehensive with comments and error handling',
                'template': self._get_detailed_template()
            },
            'conservative': {
                'name': 'Conservative',
                'description': 'Safe, well-tested patterns only',
                'template': self._get_conservative_template()
            },
            'aggressive': {
                'name': 'Aggressive',
                'description': 'Advanced features, modern patterns',
                'template': self._get_aggressive_template()
            }
        }
        
        # Performance tracking per variant
        self.variant_performance = defaultdict(lambda: {
            'attempts': 0,
            'successes': 0,
            'total_quality': 0.0,
            'total_time': 0.0
        })
        
        # Context-specific performance (property_count ranges)
        self.context_performance = defaultdict(lambda: defaultdict(lambda: {
            'attempts': 0,
            'successes': 0
        }))
        
        print("✓ Adaptive Prompt Selector initialized")
        print(f"  Available variants: {list(self.prompt_variants.keys())}")
    
    def _get_minimal_template(self) -> str:
        return """Generate AAOS HAL code for these properties:
{properties}

Requirements:
- Minimal working implementation
- AAOS 14 compliant
- Essential error handling only

Format:
{format_instructions}
"""
    
    def _get_detailed_template(self) -> str:
        return """Generate comprehensive AAOS HAL implementation for these properties:
{properties}

Requirements:
- Complete AIDL interface definitions
- Full C++ implementation with error handling
- Detailed inline comments explaining logic
- Follow AAOS 14 conventions strictly
- Include proper imports and dependencies
- Add safety checks and validation

Code Quality Standards:
- Clear variable names
- Proper error messages
- Defensive programming
- Resource cleanup

Format:
{format_instructions}

IMPORTANT: Generate production-quality code with comprehensive error handling.
"""
    
    def _get_conservative_template(self) -> str:
        return """Generate AAOS HAL code using safe, proven patterns:
{properties}

Requirements:
- Use well-established AAOS patterns only
- Avoid experimental features
- Prioritize correctness over cleverness
- Follow AOSP examples closely
- Simple, readable code

Safety Guidelines:
- Explicit error checking
- No complex abstractions
- Conservative memory management

Format:
{format_instructions}
"""
    
    def _get_aggressive_template(self) -> str:
        return """Generate modern, optimized AAOS HAL code:
{properties}

Requirements:
- Use latest AAOS 14 features
- Optimize for performance
- Modern C++ patterns (C++17/20)
- Smart pointers, RAII
- Efficient data structures

Advanced Features Welcome:
- Template metaprogramming where beneficial
- Move semantics
- Constexpr where applicable

Format:
{format_instructions}

Generate cutting-edge, performant implementation.
"""
    
    def select_variant(
        self,
        property_count: int,
        context: Optional[Dict] = None,
        exploration_rate: float = 0.1
    ) -> str:
        """
        Select best prompt variant based on history
        
        Args:
            property_count: Number of properties
            context: Additional context (module type, complexity, etc.)
            exploration_rate: Probability of trying non-optimal variant
        
        Returns:
            Selected variant name
        """
        # Get property count range for context
        prop_range = self._get_property_range(property_count)
        
        # Calculate success rate for each variant in this context
        variant_scores = {}
        total_attempts_in_context = sum(
            self.context_performance[prop_range][v]['attempts'] 
            for v in self.prompt_variants.keys()
        )
        
        for variant in self.prompt_variants.keys():
            perf = self.context_performance[prop_range][variant]
            
            if perf['attempts'] == 0:
                # No data: assign neutral score + exploration bonus
                variant_scores[variant] = 0.5 + np.random.uniform(0, 0.2)
            else:
                success_rate = perf['successes'] / perf['attempts']
                
                # Add uncertainty bonus for low-sample variants (UCB-like)
                if total_attempts_in_context > 0:
                    uncertainty = np.sqrt(
                        2 * np.log(total_attempts_in_context) / perf['attempts']
                    )
                else:
                    uncertainty = 0.5
                
                variant_scores[variant] = success_rate + 0.1 * uncertainty
        
        # Epsilon-greedy selection
        if np.random.random() < exploration_rate:
            # Explore: random variant
            selected = np.random.choice(list(self.prompt_variants.keys()))
        else:
            # Exploit: best variant
            selected = max(variant_scores, key=variant_scores.get)
        
        return selected
    
    def get_prompt(
        self,
        variant: str,
        properties: List[Dict],
        format_instructions: str = ""
    ) -> str:
        """
        Generate actual prompt from variant template
        """
        if variant not in self.prompt_variants:
            variant = 'detailed'  # Fallback
        
        template = self.prompt_variants[variant]['template']
        
        # Format properties as string
        props_str = self._format_properties(properties)
        
        # Fill template
        prompt = template.format(
            properties=props_str,
            format_instructions=format_instructions or "Generate complete, working code."
        )
        
        return prompt
    
    def _format_properties(self, properties: List[Dict]) -> str:
        """Format properties for prompt"""
        lines = []
        for prop in properties:
            lines.append(f"- Name: {prop.get('name', 'Unknown')}")
            lines.append(f"  Type: {prop.get('type', 'Unknown')}")
            lines.append(f"  Access: {prop.get('access', 'Unknown')}")
            if prop.get('description'):
                lines.append(f"  Description: {prop['description']}")
            lines.append("")
        
        return "\n".join(lines)
    
    def _get_property_range(self, count: int) -> str:
        """Bucket property counts into ranges"""
        if count <= 10:
            return "tiny"
        elif count <= 30:
            return "small"
        elif count <= 50:
            return "medium"
        elif count <= 100:
            return "large"
        else:
            return "xlarge"
    
    def update_performance(
        self,
        variant: str,
        property_count: int,
        success: bool,
        quality_score: float,
        generation_time: float
    ):
        """
        Update performance stats after generation attempt
        """
        # Update overall variant performance
        self.variant_performance[variant]['attempts'] += 1
        if success:
            self.variant_performance[variant]['successes'] += 1
        self.variant_performance[variant]['total_quality'] += quality_score
        self.variant_performance[variant]['total_time'] += generation_time
        
        # Update context-specific performance
        prop_range = self._get_property_range(property_count)
        self.context_performance[prop_range][variant]['attempts'] += 1
        if success:
            self.context_performance[prop_range][variant]['successes'] += 1
    
    def get_statistics(self) -> Dict:
        """Get performance statistics"""
        stats = {
            'overall_performance': {},
            'context_performance': {}
        }
        
        # Overall stats
        for variant, perf in self.variant_performance.items():
            if perf['attempts'] > 0:
                stats['overall_performance'][variant] = {
                    'attempts': perf['attempts'],
                    'success_rate': perf['successes'] / perf['attempts'],
                    'avg_quality': perf['total_quality'] / perf['attempts'],
                    'avg_time': perf['total_time'] / perf['attempts']
                }
        
        # Context-specific stats
        for prop_range, variants in self.context_performance.items():
            stats['context_performance'][prop_range] = {}
            for variant, perf in variants.items():
                if perf['attempts'] > 0:
                    stats['context_performance'][prop_range][variant] = {
                        'attempts': perf['attempts'],
                        'success_rate': perf['successes'] / perf['attempts']
                    }
        
        return stats
    
    def get_best_variant_for_context(self, property_count: int) -> str:
        """Get current best variant for given context"""
        prop_range = self._get_property_range(property_count)
        variants = self.context_performance[prop_range]
        
        if not variants:
            return 'detailed'  # Default
        
        best = None
        best_rate = 0.0
        
        for variant, perf in variants.items():
            if perf['attempts'] >= 3:  # Minimum sample size
                rate = perf['successes'] / perf['attempts']
                if rate > best_rate:
                    best_rate = rate
                    best = variant
        
        return best or 'detailed'
    
    def save_state(self, filepath: str):
        """Save selector state"""
        state = {
            'variant_performance': dict(self.variant_performance),
            'context_performance': {
                k: dict(v) for k, v in self.context_performance.items()
            }
        }
        
        with open(filepath, 'w') as f:
            json.dump(state, f, indent=2)
        
        print(f"✓ Prompt selector state saved to {filepath}")
    
    def load_state(self, filepath: str):
        """Load selector state"""
        with open(filepath, 'r') as f:
            state = json.load(f)
        
        self.variant_performance = defaultdict(lambda: {
            'attempts': 0, 'successes': 0, 'total_quality': 0.0, 'total_time': 0.0
        }, state['variant_performance'])
        
        self.context_performance = defaultdict(
            lambda: defaultdict(lambda: {'attempts': 0, 'successes': 0})
        )
        for prop_range, variants in state['context_performance'].items():
            for variant, perf in variants.items():
                self.context_performance[prop_range][variant] = perf
        
        print(f"✓ Prompt selector state loaded from {filepath}")


# Test code
if __name__ == "__main__":
    print("Testing Prompt Selector...\n")
    
    selector = PromptSelector()
    
    # Simulate selections
    for i in range(50):
        prop_count = np.random.randint(10, 100)
        variant = selector.select_variant(prop_count)
        
        # Simulate: 'detailed' works best for medium, 'minimal' for small
        if prop_count <= 30:
            success = (variant == 'minimal') and (np.random.random() < 0.9)
        elif prop_count <= 50:
            success = (variant == 'detailed') and (np.random.random() < 0.85)
        else:
            success = (variant == 'conservative') and (np.random.random() < 0.8)
        
        if not success:
            success = np.random.random() < 0.6
        
        quality = np.random.uniform(0.7, 0.95) if success else np.random.uniform(0.3, 0.6)
        time = np.random.uniform(30, 90)
        
        selector.update_performance(variant, prop_count, success, quality, time)
    
    stats = selector.get_statistics()
    print("\nPerformance Statistics:")
    print(json.dumps(stats, indent=2))
    
    print("\nBest variants by context:")
    for count in [10, 30, 50, 100]:
        best = selector.get_best_variant_for_context(count)
        print(f"  {count} properties: {best}")
    
    # Save state
    selector.save_state("prompt_selector_test.json")
    
    print("\n✓ Prompt Selector test complete!")