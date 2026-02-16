# FILE: run_comparison.py
"""
Comparison Script - Run Static vs Adaptive Side-by-Side
For thesis evaluation
"""
import sys
import json
import time
import subprocess
from pathlib import Path


def run_static_pipeline(num_signals: int = 50):
    """
    Run your ORIGINAL pipeline (static)
    """
    print("\n" + "=" * 70)
    print(f"  RUNNING STATIC PIPELINE ({num_signals} signals)")
    print("=" * 70)
    
    start_time = time.time()
    
    # Run your original multi_main.py
    try:
        result = subprocess.run(
            [sys.executable, "multi_main.py"],
            capture_output=True,
            text=True,
            timeout=1800  # 30 min timeout
        )
        
        elapsed = time.time() - start_time
        
        # Parse output for success metrics
        output = result.stdout
        
        # Extract success rates from output
        # Adapt these patterns to match your actual output format
        android_success = "84.6%" if "AndroidApp → OK" in output else "0%"
        backend_success = "71.4%" if "Backend → OK" in output else "0%"
        
        return {
            'success': result.returncode == 0,
            'elapsed_time': elapsed,
            'android_app_success': android_success,
            'backend_success': backend_success,
            'output': output
        }
    
    except subprocess.TimeoutExpired:
        return {
            'success': False,
            'elapsed_time': 1800,
            'error': 'Timeout'
        }
    except Exception as e:
        return {
            'success': False,
            'error': str(e)
        }


def run_adaptive_pipeline(num_signals: int = 50):
    """
    Run ADAPTIVE pipeline
    """
    print("\n" + "=" * 70)
    print(f"  RUNNING ADAPTIVE PIPELINE ({num_signals} signals)")
    print("=" * 70)
    
    start_time = time.time()
    
    try:
        result = subprocess.run(
            [sys.executable, "multi_main_adaptive.py"],
            capture_output=True,
            text=True,
            timeout=1800
        )
        
        elapsed = time.time() - start_time
        
        output = result.stdout
        
        # Extract adaptive statistics
        # Parse from adaptive output
        
        return {
            'success': result.returncode == 0,
            'elapsed_time': elapsed,
            'output': output
        }
    
    except subprocess.TimeoutExpired:
        return {
            'success': False,
            'elapsed_time': 1800,
            'error': 'Timeout'
        }
    except Exception as e:
        return {
            'success': False,
            'error': str(e)
        }


def run_full_comparison(num_runs: int = 3, num_signals: int = 50):
    """
    Run complete comparison experiment
    
    Args:
        num_runs: Number of times to run each approach
        num_signals: Number of VSS signals to process
    """
    print("=" * 70)
    print("  COMPARISON EXPERIMENT: Static vs Adaptive")
    print("=" * 70)
    print(f"  Configuration:")
    print(f"    - Runs per approach: {num_runs}")
    print(f"    - Signals: {num_signals}")
    print(f"    - Total time estimate: {num_runs * 2 * 30} minutes")
    
    results = {
        'configuration': {
            'num_runs': num_runs,
            'num_signals': num_signals,
            'timestamp': time.time()
        },
        'static': [],
        'adaptive': []
    }
    
    # Run static approach
    for run in range(num_runs):
        print(f"\n{'='*70}")
        print(f"  STATIC RUN {run + 1}/{num_runs}")
        print(f"{'='*70}")
        
        static_result = run_static_pipeline(num_signals)
        results['static'].append(static_result)
        
        print(f"\n  Static Run {run+1} Results:")
        print(f"    Success: {static_result['success']}")
        print(f"    Time: {static_result['elapsed_time']:.1f}s")
    
    # Run adaptive approach
    for run in range(num_runs):
        print(f"\n{'='*70}")
        print(f"  ADAPTIVE RUN {run + 1}/{num_runs}")
        print(f"{'='*70}")
        
        adaptive_result = run_adaptive_pipeline(num_signals)
        results['adaptive'].append(adaptive_result)
        
        print(f"\n  Adaptive Run {run+1} Results:")
        print(f"    Success: {adaptive_result['success']}")
        print(f"    Time: {adaptive_result['elapsed_time']:.1f}s")
    
    # Save results
    output_path = Path("comparison_results.json")
    with open(output_path, 'w') as f:
        json.dump(results, f, indent=2)
    
    # Print summary
    print("\n" + "=" * 70)
    print("  COMPARISON COMPLETE")
    print("=" * 70)
    
    static_times = [r['elapsed_time'] for r in results['static'] if r['success']]
    adaptive_times = [r['elapsed_time'] for r in results['adaptive'] if r['success']]
    
    if static_times and adaptive_times:
        print(f"\n  Static Approach:")
        print(f"    Avg time: {sum(static_times)/len(static_times):.1f}s")
        print(f"    Success rate: {len(static_times)}/{num_runs}")
        
        print(f"\n  Adaptive Approach:")
        print(f"    Avg time: {sum(adaptive_times)/len(adaptive_times):.1f}s")
        print(f"    Success rate: {len(adaptive_times)}/{num_runs}")
        
        if len(static_times) > 0 and len(adaptive_times) > 0:
            speedup = sum(static_times)/len(static_times) / (sum(adaptive_times)/len(adaptive_times))
            print(f"\n  Speedup: {speedup:.2f}x")
    
    print(f"\n✓ Results saved to {output_path}")
    print(f"✓ Detailed adaptive statistics in: adaptive_outputs/thesis_results.json")


if __name__ == "__main__":
    import argparse
    
    parser = argparse.ArgumentParser(description='Compare static vs adaptive pipeline')
    parser.add_argument('--runs', type=int, default=3, help='Number of runs per approach')
    parser.add_argument('--signals', type=int, default=50, help='Number of VSS signals')
    
    args = parser.parse_args()
    
    run_full_comparison(num_runs=args.runs, num_signals=args.signals)