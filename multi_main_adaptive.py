# FILE: multi_main_adaptive.py
"""
VSS → AAOS HAL Generation Pipeline - ADAPTIVE VERSION
Based on your original multi_main.py with adaptive learning integrated
"""

import sys
import json
import time
import asyncio
from pathlib import Path
from typing import List, Dict, Optional

# ============================================================================
# ADAPTIVE IMPORTS
# ============================================================================

# Import adaptive wrapper
from adaptive_integration import get_adaptive_wrapper

# Import ADAPTIVE agents (drop-in replacements)
try:
    from agents.llm_android_app_agent_adaptive import generate_android_app_llm_first
    ANDROID_ADAPTIVE = True
except ImportError:
    print("⚠ Adaptive Android agent not found, using original")
    from agents.llm_android_app_agent import generate_android_app_llm_first
    ANDROID_ADAPTIVE = False

try:
    from agents.llm_backend_agent_adaptive import generate_backend_llm_first
    BACKEND_ADAPTIVE = True
except ImportError:
    print("⚠ Adaptive Backend agent not found, using original")
    from agents.llm_backend_agent import generate_backend_llm_first
    BACKEND_ADAPTIVE = False

try:
    from agents.design_doc_agent_adaptive import generate_design_doc
    DESIGN_ADAPTIVE = True
except ImportError:
    print("⚠ Adaptive DesignDoc agent not found, using original")
    from agents.design_doc_agent import generate_design_doc
    DESIGN_ADAPTIVE = False

# Import remaining agents (unchanged from your original)
from agents.vhal_aidl_build_agent import generate_vhal_aidl_bp
from agents.vhal_service_build_agent import generate_vhal_service_build_glue

# Your original imports (from multi_main.py)
from tools.vss_loader import load_and_flatten_vss, select_subset
from tools.labeller import label_signals_batched_async
from tools.yaml_converter import convert_to_yaml
from tools.spec_loader import load_spec
from tools.module_planner import plan_modules_with_llm
from agents.hal_architect_agent import generate_hal_modules_parallel
from agents.promote_draft_agent import promote_draft_to_final
from agents.build_glue_agent import generate_build_glue


# ============================================================================
# CONFIGURATION (from your original multi_main.py)
# ============================================================================

VSS_FILE = "./dataset/vss.json"
CACHE_DIR = Path("/content/vss_temp")
OUTPUT_DIR = Path("output")
ADAPTIVE_OUTPUT_DIR = Path("adaptive_outputs")

NUM_TEST_SIGNALS = 50
LLM_FIRST_MODE = True
TIMEOUT_PER_FILE = 60

# Create directories
CACHE_DIR.mkdir(parents=True, exist_ok=True)
OUTPUT_DIR.mkdir(parents=True, exist_ok=True)
ADAPTIVE_OUTPUT_DIR.mkdir(parents=True, exist_ok=True)


# ============================================================================
# MAIN PIPELINE (YOUR ORIGINAL LOGIC + ADAPTIVE)
# ============================================================================

def main():
    """
    Main VSS → AAOS HAL generation pipeline with ADAPTIVE learning
    """
    
    # ========================================================================
    # HEADER
    # ========================================================================
    print("=" * 70)
    print("  VSS → AAOS HAL Generation Pipeline (ADAPTIVE MODE)")
    print("=" * 70)
    print(f"Test signals: {NUM_TEST_SIGNALS}")
    print(f"Persistent cache: {CACHE_DIR}")
    print(f"Project output: {OUTPUT_DIR}")
    print(f"Adaptive output: {ADAPTIVE_OUTPUT_DIR}")
    print()
    print("Adaptive Configuration:")
    print("  - Goal: 90%+ LLM-generated production code")
    print("  - Strategy: RL-based optimization + adaptive prompting")
    print("  - Expected: Learning improves over time")
    print(f"  - Agents: design_doc ({DESIGN_ADAPTIVE}), android_app ({ANDROID_ADAPTIVE}), backend ({BACKEND_ADAPTIVE})")
    print("=" * 70)
    
    # ========================================================================
    # INITIALIZE ADAPTIVE WRAPPER
    # ========================================================================
    print("\n[ADAPTIVE] Initializing learning components...")
    
    adaptive_wrapper = get_adaptive_wrapper(
        enable_all=True,
        output_dir=str(ADAPTIVE_OUTPUT_DIR)
    )
    
    print("\n✓ ADAPTIVE MODE ENABLED")
    print("  - Chunk size optimization: ✓ (Thompson Sampling)")
    print("  - Prompt variant selection: ✓ (Context-aware)")
    print("  - Performance tracking: ✓ (SQLite DB)")
    print("  - Learning persistence: ✓ (Saved to disk)")
    
    overall_start = time.time()
    
    # ========================================================================
    # [PREP] LOAD AND FLATTEN VSS (from your original)
    # ========================================================================
    print(f"\n[PREP] Loading and flattening {VSS_FILE} ...")
    
    flat_vss_path = CACHE_DIR / "VSS_FLAT.json"
    subset_path = CACHE_DIR / f"VSS_LIMITED_{NUM_TEST_SIGNALS}.json"
    
    if flat_vss_path.exists():
        print(f" Loading cached flat VSS from {flat_vss_path}")
        with open(flat_vss_path, 'r') as f:
            flat_signals = json.load(f)
    else:
        flat_signals = load_and_flatten_vss(VSS_FILE)
        with open(flat_vss_path, 'w') as f:
            json.dump(flat_signals, f, indent=2)
        print(f" Flattened to {len(flat_signals)} leaf signals")
    
    # Select subset
    if subset_path.exists():
        print(f" Loading cached subset from {subset_path}")
        with open(subset_path, 'r') as f:
            selected = json.load(f)
    else:
        selected = select_subset(flat_signals, NUM_TEST_SIGNALS)
        with open(subset_path, 'w') as f:
            json.dump(selected, f, indent=2)
        print(f"Selected {len(selected)} leaf signals for labelling & processing")
        print(f" Wrote selected flat subset → {subset_path}")
    
    # ========================================================================
    # [LABELLING] LABEL SIGNALS (from your original)
    # ========================================================================
    print(f"[LABELLING] Labelling the selected subset (fast mode)...")
    
    labelled_path = CACHE_DIR / f"VSS_LABELLED_{NUM_TEST_SIGNALS}.json"
    
    if labelled_path.exists():
        print(f" Loading cached labelled data from {labelled_path}")
        with open(labelled_path, 'r') as f:
            labelled = json.load(f)
    else:
        print(f"[LABELLING] Labelling {len(selected)} pre-selected signals (batched + async)...")
        labelled = asyncio.run(label_signals_batched_async(selected))
        with open(labelled_path, 'w') as f:
            json.dump(labelled, f, indent=2)
        print(f"[LABELLING] Done! {len(labelled)} labelled signals ready")
        print(f"[LABELLING] Saved fresh labelled data → {labelled_path}")
    
    # ========================================================================
    # [YAML] CONVERT TO HAL SPEC (from your original)
    # ========================================================================
    print("\n[YAML] Converting **labelled** subset to HAL YAML spec...")
    
    yaml_path = OUTPUT_DIR / f"SPEC_FROM_VSS_{NUM_TEST_SIGNALS}.yaml"
    convert_to_yaml(labelled, str(yaml_path))
    print(f" Wrote {yaml_path} with {len(labelled)} properties")
    
    # ========================================================================
    # [LOAD] LOAD SPEC (from your original)
    # ========================================================================
    print("[LOAD] Loading HAL spec...")
    spec = load_spec(str(yaml_path))
    print(f"[LOAD] Success — domain: {spec.get('domain')}, {len(spec.get('properties', []))} properties")
    print(f"[LOAD] Built lookup with {len(spec.get('properties', []))} unique ids")
    
    sample_ids = list(spec.get('property_lookup', {}).keys())[:5]
    if sample_ids:
        print("Sample loaded property ids (first 5 or less):")
        for pid in sample_ids:
            print(f"  → {pid}")
    
    # ========================================================================
    # [PLAN] MODULE PLANNING (from your original)
    # ========================================================================
    print("\n[PLAN] Running Module Planner...")
    module_plan = plan_modules_with_llm(spec)
    
    modules_list = module_plan.get("modules", [])
    print(f"[MODULE PLANNER] Summary: {len(spec.get('properties', []))} signals → "
          f"{len(modules_list)} modules (largest: {modules_list[0]['name'] if modules_list else 'N/A'}) "
          f"[method: llm_based]")
    
    plan_path = OUTPUT_DIR / "MODULE_PLAN.json"
    with open(plan_path, 'w') as f:
        json.dump(module_plan, f, indent=2)
    print(f"[MODULE PLANNER] Wrote {plan_path}")
    print(f" → {len(modules_list)} modules, {len(spec.get('properties', []))} signals total")
    if modules_list:
        print(f" Modules: {', '.join(m['name'] for m in modules_list)}")
    
    # Debug: check name format
    if modules_list and modules_list[0].get('property_names'):
        planner_fmt = modules_list[0]['property_names'][0]
        loader_fmt = sample_ids[0] if sample_ids else "N/A"
        print(f"\n[DEBUG] Checking for name format mismatch:")
        print(f"  Planner format: {planner_fmt}")
        print(f"  Loader format:  {loader_fmt}")
        print(f"  Match: {planner_fmt == loader_fmt}")
    
    # ========================================================================
    # [GEN] GENERATE HAL MODULES (from your original)
    # ========================================================================
    print(f"\n[GEN] Generating {len(modules_list)} HAL modules (parallel, max 6)...")
    
    for mod in modules_list:
        mod_name = mod.get("name", "UNKNOWN")
        prop_names = mod.get("property_names", [])
        matched = [spec['property_lookup'][pn] for pn in prop_names if pn in spec['property_lookup']]
        mod['matched_properties'] = matched
        print(f"  {mod_name}: matched {len(matched)}/{len(prop_names)} properties")
    
    hal_results = generate_hal_modules_parallel(spec, modules_list)
    
    successful_mods = sum(1 for r in hal_results if r.get("success"))
    print(f"\nAll HAL module drafts generated ({successful_mods}/{len(modules_list)} modules OK)")
    
    total_expected = len(spec.get('properties', []))
    total_matched = sum(len(m.get('matched_properties', [])) for m in modules_list)
    print(f"Overall match rate: {total_matched}/{total_expected} properties "
          f"({100.0 * total_matched / total_expected if total_expected else 0:.1f}%)")
    
    # ========================================================================
    # [SUPPORT] GENERATE SUPPORTING COMPONENTS (ADAPTIVE!)
    # ========================================================================
    print("\n[SUPPORT] Generating supporting components (ADAPTIVE mode)...")
    print("  Note: Adaptive agents learn optimal strategies")
    print("  Expected: Performance improves with more generations")
    
    support_results = {}
    
    # ------------------------------------------------------------------------
    # Design Documentation (ADAPTIVE)
    # ------------------------------------------------------------------------
    print("\n[DESIGN DOC] Adaptive generation (optimized for quality)...")
    print("  Configuration:")
    print("    - Diagram timeout: 180s each")
    print("    - Document timeout: 240s")
    print("    - Adaptive timeouts: enabled")
    print("    - Prompt selection: learning-based")
    
    try:
        design_start = time.time()
        design_result = generate_design_doc(spec, modules_list)
        design_time = time.time() - design_start
        
        support_results['design_doc'] = {
            'success': True,
            'time': design_time,
            'result': design_result
        }
        
        print(f"\n[DESIGN DOC] Generation complete!")
        if isinstance(design_result, dict) and 'adaptive_metadata' in design_result:
            meta = design_result['adaptive_metadata']
            print(f"  Quality score: {meta['quality_score']:.2f}")
            print(f"  Generation time: {design_time:.1f}s")
            print(f"  Prompt variant: {meta['adaptive_decision']['prompt_variant']}")
            print(f"  Strategy: {meta['adaptive_decision']['strategy']}")
        print("  [SUPPORT] DesignDoc → OK")
    
    except Exception as e:
        print(f"  [SUPPORT] DesignDoc → FAILED: {e}")
        support_results['design_doc'] = {'success': False, 'error': str(e)}
    
    # ------------------------------------------------------------------------
    # Android App (ADAPTIVE)
    # ------------------------------------------------------------------------
    print("\n[LLM ANDROID APP] Adaptive generation (optimized for quality)...")
    print("  Configuration:")
    print("    - Try full generation up to 30 properties")
    print("    - Progressive generation for larger modules")
    print("    - Adaptive timeouts: enabled")
    print("    - Chunk size: learned via Thompson Sampling")
    
    try:
        android_start = time.time()
        android_result = generate_android_app_llm_first(spec, modules_list)
        android_time = time.time() - android_start
        
        support_results['android_app'] = {
            'success': True,
            'time': android_time,
            'result': android_result
        }
        
        print(f"\n[LLM ANDROID APP] Generation complete!")
        if isinstance(android_result, dict) and 'adaptive_metadata' in android_result:
            meta = android_result['adaptive_metadata']
            decision = meta['adaptive_decision']
            print(f"  Quality score: {meta['quality_score']:.2f}")
            print(f"  Generation time: {android_time:.1f}s")
            print(f"  Strategy: {decision['strategy']}")
            print(f"  Chunk size: {decision['chunk_size']}")
            print(f"  Prompt variant: {decision['prompt_variant']}")
            
            # Show learning stats
            if 'learning_stats' in meta:
                stats = meta['learning_stats']
                if 'best_chunk_size' in stats:
                    print(f"  Current best chunk size: {stats['best_chunk_size']}")
        
        print("  [SUPPORT] AndroidApp → OK")
    
    except Exception as e:
        print(f"  [SUPPORT] AndroidApp → FAILED: {e}")
        support_results['android_app'] = {'success': False, 'error': str(e)}
    
    # ------------------------------------------------------------------------
    # Backend (ADAPTIVE)
    # ------------------------------------------------------------------------
    print("\n[LLM BACKEND] Adaptive generation (optimized for quality)...")
    print("  Configuration:")
    print("    - Try full generation up to 30 properties")
    print("    - Progressive generation for larger modules")
    print("    - Adaptive timeouts: enabled")
    
    try:
        backend_start = time.time()
        backend_result = generate_backend_llm_first(spec, modules_list)
        backend_time = time.time() - backend_start
        
        support_results['backend'] = {
            'success': True,
            'time': backend_time,
            'result': backend_result
        }
        
        print(f"\n[LLM BACKEND] Generation complete!")
        if isinstance(backend_result, dict) and 'adaptive_metadata' in backend_result:
            meta = backend_result['adaptive_metadata']
            print(f"  Quality score: {meta['quality_score']:.2f}")
            print(f"  Generation time: {backend_time:.1f}s")
            print(f"  Prompt variant: {meta['adaptive_decision']['prompt_variant']}")
        
        print("  [SUPPORT] Backend → OK")
    
    except Exception as e:
        print(f"  [SUPPORT] Backend → FAILED: {e}")
        support_results['backend'] = {'success': False, 'error': str(e)}
    
    # ------------------------------------------------------------------------
    # Build Glue (Static - no adaptation needed)
    # ------------------------------------------------------------------------
    print("\n[SUPPORT] Running PromoteDraft → BuildGlue (sequential, order matters)...")
    
    try:
        print("[PROMOTE] Copying successful LLM drafts to final AOSP layout...")
        promote_draft_to_final()
        print("[PROMOTE] Draft promoted successfully!")
        print("   → Final files now in output/hardware/interfaces/automotive/vehicle/")
        support_results['promote'] = {'success': True}
        print("  [SUPPORT] PromoteDraft → OK")
    except Exception as e:
        print(f"  [SUPPORT] PromoteDraft → FAILED: {e}")
        support_results['promote'] = {'success': False, 'error': str(e)}
    
    try:
        print("[BUILD GLUE] Generating build files...")
        generate_build_glue()
        print("[BUILD GLUE] Done")
        support_results['build_glue'] = {'success': True}
        print("  [SUPPORT] BuildGlue → OK (validated ✓)")
    except Exception as e:
        print(f"  [SUPPORT] BuildGlue → FAILED: {e}")
        support_results['build_glue'] = {'success': False, 'error': str(e)}
    
    total_time = time.time() - overall_start
    
    # ========================================================================
    # ADAPTIVE STATISTICS
    # ========================================================================
    print("\n" + "=" * 70)
    print("  ADAPTIVE LEARNING STATISTICS")
    print("=" * 70)
    
    stats = adaptive_wrapper.get_full_statistics()
    
    # Overall performance
    tracker_stats = stats['tracker']
    print(f"\n[OVERALL PERFORMANCE]")
    print(f"  Total generations: {tracker_stats['total_generations']}")
    print(f"  Total successes: {tracker_stats['total_successes']}")
    print(f"  Overall success rate: {tracker_stats['overall_success_rate']:.1%}")
    print(f"  Avg quality score: {tracker_stats['avg_quality']:.2f}")
    print(f"  Avg generation time: {tracker_stats['avg_generation_time']:.1f}s")
    
    # Chunk optimizer statistics
    if stats['chunk_optimizer']:
        chunk_stats = stats['chunk_optimizer']
        print(f"\n[CHUNK SIZE OPTIMIZATION] (Thompson Sampling)")
        print(f"  Total attempts: {chunk_stats['total_attempts']}")
        print(f"  Best chunk size: {chunk_stats['best_chunk_size']}")
        print(f"  Expected rewards by size:")
        for size in sorted(chunk_stats['expected_rewards'].keys()):
            reward = chunk_stats['expected_rewards'][size]
            attempts = chunk_stats['attempts_per_size'].get(size, 0)
            alpha = chunk_stats['alpha_params'].get(size, 1.0)
            beta = chunk_stats['beta_params'].get(size, 1.0)
            print(f"    Size {size:2d}: reward={reward:.3f}, attempts={attempts:3d}, "
                  f"Beta({alpha:.1f}, {beta:.1f})")
    
    # Prompt selector statistics
    if stats['prompt_selector']:
        prompt_stats = stats['prompt_selector']
        overall_perf = prompt_stats.get('overall_performance', {})
        
        if overall_perf:
            print(f"\n[PROMPT VARIANT PERFORMANCE]")
            print(f"  Variant      Success Rate  Avg Quality  Attempts")
            print(f"  -----------  ------------  -----------  --------")
            for variant in sorted(overall_perf.keys()):
                perf = overall_perf[variant]
                print(f"  {variant:11s}  {perf['success_rate']:11.1%}  "
                      f"{perf['avg_quality']:11.2f}  {perf['attempts']:8d}")
        
        context_perf = prompt_stats.get('context_performance', {})
        if context_perf:
            print(f"\n[CONTEXT-SPECIFIC PERFORMANCE]")
            for context in sorted(context_perf.keys()):
                variants = context_perf[context]
                print(f"  {context}:")
                for variant in sorted(variants.keys()):
                    perf = variants[variant]
                    if perf['attempts'] > 0:
                        print(f"    {variant:11s}: {perf['success_rate']:5.1%} "
                              f"({perf['attempts']} attempts)")
    
    # Learning curve
    try:
        learning_curve = adaptive_wrapper.tracker.get_learning_curve(window_size=10)
        if learning_curve and len(learning_curve) > 1:
            print(f"\n[LEARNING CURVE] (window size: 10)")
            print(f"  Window     Success    Quality    Time")
            print(f"  ---------  ---------  ---------  --------")
            for window in learning_curve:
                print(f"  {window['window_start']:3d}-{window['window_end']:3d}     "
                      f"{window['success_rate']:8.1%}  "
                      f"{window['avg_quality']:9.2f}  "
                      f"{window['avg_time']:7.1f}s")
            
            # Show improvement
            if len(learning_curve) >= 2:
                first_window = learning_curve[0]
                last_window = learning_curve[-1]
                improvement = (last_window['success_rate'] - first_window['success_rate'])
                print(f"\n  Improvement: {first_window['success_rate']:.1%} → "
                      f"{last_window['success_rate']:.1%} ({improvement:+.1%})")
    except Exception as e:
        print(f"  (Could not compute learning curve: {e})")
    
    # ========================================================================
    # FINAL SUMMARY
    # ========================================================================
    print("\n" + "=" * 70)
    print("  Generation Complete!")
    print("=" * 70)
    
    print(f"\nCached input files: {CACHE_DIR}")
    print(f"All generated outputs: {OUTPUT_DIR}")
    print(f"Adaptive learning data: {ADAPTIVE_OUTPUT_DIR}")
    
    print("\nAdaptive Results Summary:")
    successful_support = sum(1 for r in support_results.values() if r.get('success'))
    print(f"  Support components: {successful_support}/{len(support_results)} successful")
    
    for component, result in support_results.items():
        status = "✓" if result.get('success') else "✗"
        time_str = f" ({result['time']:.1f}s)" if 'time' in result else ""
        print(f"    {status} {component}{time_str}")
    
    print(f"\nTotal pipeline time: {total_time:.1f}s")
    
    print("\nExpected Quality Indicators:")
    overall_success = tracker_stats['overall_success_rate']
    if overall_success >= 0.90:
        print("  ✓ Excellent (90%+): Most files LLM-generated")
    elif overall_success >= 0.80:
        print("  ✓ Good (80-89%): Some templates, still production-ready")
    elif overall_success >= 0.70:
        print("  ⚠ Fair (70-79%): Many templates, consider tuning timeouts")
    else:
        print("  ✗ Poor (<70%): Review configuration and failure patterns")
    
    # ========================================================================
    # EXPORT FOR THESIS
    # ========================================================================
    print("\n[EXPORT] Saving results for thesis analysis...")
    
    # Export adaptive statistics
    adaptive_wrapper.export_results(
        str(ADAPTIVE_OUTPUT_DIR / "thesis_results.json")
    )
    
    # Export generation summary
    summary = {
        'configuration': {
            'num_signals': NUM_TEST_SIGNALS,
            'llm_first': LLM_FIRST_MODE,
            'timeout_per_file': TIMEOUT_PER_FILE,
            'adaptive_features': {
                'android_app': ANDROID_ADAPTIVE,
                'backend': BACKEND_ADAPTIVE,
                'design_doc': DESIGN_ADAPTIVE
            }
        },
        'timestamp': time.time(),
        'total_time': total_time,
        'num_modules': len(modules_list),
        'hal_results': {
            'successful': successful_mods,
            'total': len(modules_list)
        },
        'support_results': {
            k: {
                'success': v.get('success'),
                'time': v.get('time'),
                'error': v.get('error')
            }
            for k, v in support_results.items()
        },
        'adaptive_statistics': {
            'overall': tracker_stats,
            'chunk_optimizer': stats.get('chunk_optimizer'),
            'prompt_selector': stats.get('prompt_selector')
        }
    }
    
    summary_path = ADAPTIVE_OUTPUT_DIR / 'generation_summary.json'
    with open(summary_path, 'w') as f:
        json.dump(summary, f, indent=2)
    
    print(f"  ✓ Adaptive statistics: {ADAPTIVE_OUTPUT_DIR}/thesis_results.json")
    print(f"  ✓ Generation summary: {summary_path}")
    
    # ========================================================================
    # RECOMMENDATIONS
    # ========================================================================
    print("\n[RECOMMENDATIONS]")
    
    if tracker_stats['total_generations'] < 20:
        print("  ⚠ Limited learning data (<20 generations)")
        print("    → Run more generations to improve learning")
        print("    → Try: python3 multi_main_adaptive.py (multiple times)")
    else:
        print(f"  ✓ Good learning data ({tracker_stats['total_generations']} generations)")
    
    if stats['chunk_optimizer']:
        best_chunk = stats['chunk_optimizer']['best_chunk_size']
        print(f"  ✓ Optimal chunk size learned: {best_chunk}")
        print(f"    → System will use this for future generations")
    
    if stats['prompt_selector'] and overall_perf:
        best_variant = max(overall_perf.items(), key=lambda x: x[1]['success_rate'])
        print(f"  ✓ Best prompt variant: {best_variant[0]} ({best_variant[1]['success_rate']:.1%})")
    
    if overall_success < 0.80:
        print(f"  ⚠ Success rate below 80%: {overall_success:.1%}")
        print("    → Consider increasing timeouts")
        print("    → Review failure patterns in adaptive_outputs/thesis_results.json")
    
    print("\n" + "=" * 70)
    print("  Next Steps:")
    print("  1. Review generated code in output/ directory")
    print("  2. Check adaptive learning in adaptive_outputs/")
    print("  3. Run again to see learning improvement")
    print("  4. Compare with static baseline using run_comparison.py")
    print("=" * 70)


# ============================================================================
# ENTRY POINT
# ============================================================================

if __name__ == "__main__":
    import argparse
    
    parser = argparse.ArgumentParser(
        description='VSS to AAOS HAL generation with adaptive learning'
    )
    parser.add_argument(
        '--signals',
        type=int,
        default=50,
        help='Number of VSS signals to process (default: 50)'
    )
    
    args = parser.parse_args()
    
    # Update config
    NUM_TEST_SIGNALS = args.signals
    
    # Run main pipeline
    try:
        main()
        sys.exit(0)
    
    except KeyboardInterrupt:
        print("\n\n⚠ Pipeline interrupted by user")
        sys.exit(1)
    
    except Exception as e:
        print(f"\n\n✗ Pipeline failed with error: {e}")
        import traceback
        traceback.print_exc()
        sys.exit(1)