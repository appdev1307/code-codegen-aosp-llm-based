# FILE: adaptive_components/chunk_size_optimizer.py
"""
Chunk Size Optimizer - Uses Thompson Sampling (RL) to learn optimal chunk sizes
Complete, production-ready implementation
"""
import numpy as np
from typing import List, Dict, Tuple
import json
from pathlib import Path


class ThompsonSamplingOptimizer:
    """
    Multi-Armed Bandit using Thompson Sampling for chunk size selection
    
    Each "arm" is a chunk size option (10, 15, 20, 25, 30)
    Reward based on: success + quality - time penalty
    """
    
    def __init__(
        self, 
        chunk_sizes: List[int] = None,
        alpha_prior: float = 1.0,
        beta_prior: float = 1.0
    ):
        """
        Args:
            chunk_sizes: Available chunk size options
            alpha_prior: Prior for Beta distribution (successes)
            beta_prior: Prior for Beta distribution (failures)
        """
        self.chunk_sizes = chunk_sizes or [10, 15, 20, 25, 30]
        
        # Beta distribution parameters for each arm
        # Beta(α, β) models success probability
        self.alpha = {size: alpha_prior for size in self.chunk_sizes}
        self.beta = {size: beta_prior for size in self.chunk_sizes}
        
        # Track attempts per arm
        self.attempts = {size: 0 for size in self.chunk_sizes}
        
        # Reward history
        self.reward_history = {size: [] for size in self.chunk_sizes}
        
        print(f"✓ Thompson Sampling Optimizer initialized")
        print(f"  Chunk size options: {self.chunk_sizes}")
    
    def select_chunk_size(
        self, 
        property_count: int,
        exploration_bonus: float = 0.0
    ) -> int:
        """
        Select chunk size using Thompson Sampling
        
        Args:
            property_count: Number of properties to generate
            exploration_bonus: Bonus for underexplored arms
        
        Returns:
            Selected chunk size
        """
        # Filter valid chunk sizes (not larger than property count)
        valid_sizes = [s for s in self.chunk_sizes if s <= property_count]
        
        if not valid_sizes:
            # Fallback for very small modules
            return min(self.chunk_sizes)
        
        # Thompson Sampling: sample from Beta distribution for each arm
        samples = {}
        for size in valid_sizes:
            # Sample expected reward from Beta(α, β)
            theta = np.random.beta(self.alpha[size], self.beta[size])
            
            # Add exploration bonus for underexplored arms
            if self.attempts[size] < 5:  # Warm-up period
                theta += exploration_bonus
            
            samples[size] = theta
        
        # Select arm with highest sampled value
        selected = max(samples, key=samples.get)
        
        self.attempts[selected] += 1
        
        return selected
    
    def update_reward(
        self,
        chunk_size: int,
        success: bool,
        quality_score: float,
        generation_time: float,
        time_penalty_weight: float = 0.01
    ):
        """
        Update belief about chunk size based on observed reward
        
        Reward function:
          R = success_bonus + quality_bonus - time_penalty
        """
        # Calculate reward components
        success_bonus = 100.0 if success else 0.0
        quality_bonus = quality_score * 50.0 if success else 0.0
        time_penalty = generation_time * time_penalty_weight
        
        total_reward = success_bonus + quality_bonus - time_penalty
        
        # Normalize reward to [0, 1] for Beta update
        # Max possible: 100 + 50 = 150, normalize to probability
        normalized_reward = min(max(total_reward / 150.0, 0.0), 1.0)
        
        # Update Beta distribution parameters
        # Treat as Bernoulli trial with probability = normalized_reward
        if normalized_reward > 0.5:
            # Good outcome: increase α (successes)
            self.alpha[chunk_size] += normalized_reward
        else:
            # Poor outcome: increase β (failures)
            self.beta[chunk_size] += (1.0 - normalized_reward)
        
        # Store reward history
        self.reward_history[chunk_size].append({
            'success': success,
            'quality': quality_score,
            'time': generation_time,
            'reward': total_reward,
            'normalized_reward': normalized_reward
        })
    
    def get_expected_rewards(self) -> Dict[int, float]:
        """
        Get expected reward (mean of Beta distribution) for each arm
        """
        expected = {}
        for size in self.chunk_sizes:
            # Mean of Beta(α, β) = α / (α + β)
            expected[size] = self.alpha[size] / (self.alpha[size] + self.beta[size])
        
        return expected
    
    def get_confidence_intervals(self, confidence: float = 0.95) -> Dict[int, Tuple[float, float]]:
        """
        Get confidence intervals for each arm
        """
        intervals = {}
        for size in self.chunk_sizes:
            # Use Beta distribution quantiles
            lower_percentile = (1 - confidence) * 50
            upper_percentile = (1 + confidence) * 50
            
            samples = np.random.beta(self.alpha[size], self.beta[size], 10000)
            intervals[size] = (
                np.percentile(samples, lower_percentile),
                np.percentile(samples, upper_percentile)
            )
        
        return intervals
    
    def get_best_chunk_size(self) -> int:
        """
        Get current best estimate (highest expected reward)
        """
        expected = self.get_expected_rewards()
        return max(expected, key=expected.get)
    
    def get_statistics(self) -> Dict:
        """
        Get optimizer statistics
        """
        expected = self.get_expected_rewards()
        
        stats = {
            'total_attempts': sum(self.attempts.values()),
            'attempts_per_size': dict(self.attempts),
            'expected_rewards': expected,
            'best_chunk_size': self.get_best_chunk_size(),
            'alpha_params': dict(self.alpha),
            'beta_params': dict(self.beta)
        }
        
        return stats
    
    def save_state(self, filepath: str):
        """Save optimizer state for persistence"""
        state = {
            'chunk_sizes': self.chunk_sizes,
            'alpha': self.alpha,
            'beta': self.beta,
            'attempts': self.attempts,
            'reward_history': self.reward_history
        }
        
        with open(filepath, 'w') as f:
            json.dump(state, f, indent=2)
        
        print(f"✓ Optimizer state saved to {filepath}")
    
    def load_state(self, filepath: str):
        """Load optimizer state"""
        with open(filepath, 'r') as f:
            state = json.load(f)
        
        self.chunk_sizes = state['chunk_sizes']
        self.alpha = {int(k): v for k, v in state['alpha'].items()}
        self.beta = {int(k): v for k, v in state['beta'].items()}
        self.attempts = {int(k): v for k, v in state['attempts'].items()}
        self.reward_history = {int(k): v for k, v in state['reward_history'].items()}
        
        print(f"✓ Optimizer state loaded from {filepath}")


# Test code
if __name__ == "__main__":
    print("Testing Thompson Sampling Optimizer...\n")
    
    optimizer = ThompsonSamplingOptimizer()
    
    # Simulate 100 generations
    # Ground truth: chunk_size=20 is actually optimal
    for i in range(100):
        property_count = np.random.randint(20, 100)
        chunk_size = optimizer.select_chunk_size(property_count)
        
        # Simulate outcome (20 is best)
        if chunk_size == 20:
            success_prob = 0.9
        elif chunk_size in [15, 25]:
            success_prob = 0.75
        else:
            success_prob = 0.6
        
        success = np.random.random() < success_prob
        quality = np.random.uniform(0.7, 0.95) if success else np.random.uniform(0.3, 0.6)
        time = np.random.uniform(30, 90)
        
        optimizer.update_reward(chunk_size, success, quality, time)
        
        if (i + 1) % 20 == 0:
            stats = optimizer.get_statistics()
            print(f"\nAfter {i+1} generations:")
            print(f"  Expected rewards: {stats['expected_rewards']}")
            print(f"  Best chunk size: {stats['best_chunk_size']}")
            print(f"  Attempts: {stats['attempts_per_size']}")
    
    # Final statistics
    print("\n" + "="*60)
    print("FINAL RESULTS")
    print("="*60)
    stats = optimizer.get_statistics()
    print(f"Best chunk size: {stats['best_chunk_size']}")
    print(f"Expected rewards: {stats['expected_rewards']}")
    
    # Save state
    optimizer.save_state("optimizer_state_test.json")
    
    print("\n✓ Optimizer test complete!")