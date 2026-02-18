# FILE: agents/llm_backend_agent_adaptive.py
"""
Backend Agent - ADAPTIVE VERSION

What this actually changes vs original:
  - TRY_FULL_FIRST_THRESHOLD → learned by Thompson Sampling RL
  - PROGRESSIVE_BATCH_SIZE   → learned by Thompson Sampling RL
  - prompt style             → learned by prompt selector

Everything else is identical to llm_backend_agent.py
"""

import asyncio
import time
from pathlib import Path
from typing import List, Optional, Tuple

from llm_client import call_llm
from tools.safe_writer import SafeWriter

# ============================================================================
# ADAPTIVE IMPORTS
# ============================================================================
from adaptive_integration import get_adaptive_wrapper
import agents.llm_backend_agent as _base

# ============================================================================
# COPY ALL CONSTANTS FROM ORIGINAL
# ============================================================================
SYSTEM_PROMPT = _base.SYSTEM_PROMPT
_EXECUTOR   = _base._EXECUTOR

TIMEOUT_STATIC_FILE       = _base.TIMEOUT_STATIC_FILE
TIMEOUT_MODEL_SMALL       = _base.TIMEOUT_MODEL_SMALL
TIMEOUT_MODEL_LARGE       = _base.TIMEOUT_MODEL_LARGE
TIMEOUT_SIMULATOR_SMALL   = _base.TIMEOUT_SIMULATOR_SMALL
TIMEOUT_SIMULATOR_LARGE   = _base.TIMEOUT_SIMULATOR_LARGE
TIMEOUT_MAIN              = _base.TIMEOUT_MAIN

# These are what adaptive system controls:
TRY_FULL_FIRST_THRESHOLD  = _base.TRY_FULL_FIRST_THRESHOLD   # default 30
PROGRESSIVE_BATCH_SIZE    = _base.PROGRESSIVE_BATCH_SIZE      # default 15

# Reuse all helpers from original
_timeout_manager          = _base._timeout_manager
_build_compact_props      = _base._build_compact_props
_build_detailed_props     = _base._build_detailed_props
_index_properties         = _base._index_properties
_call_async               = _base._call_async
_gen_requirements_llm     = _base._gen_requirements_llm
_gen_config_llm           = _base._gen_config_llm
_gen_main_llm             = _base._gen_main_llm
_get_template_requirements = _base._get_template_requirements
_get_template_config      = _base._get_template_config
_get_template_main        = _base._get_template_main
_get_template_model       = _base._get_template_model
_get_template_simulator   = _base._get_template_simulator


# ============================================================================
# ADAPTIVE PROMPT BUILDER
# ============================================================================
def _build_adaptive_props(prop_names, properties_by_name, variant):
    """Select prompt detail level based on learned variant"""
    if variant in ('minimal', 'conservative'):
        base = _build_compact_props(prop_names, properties_by_name, max_show=5)
        if variant == 'conservative':
            base += "\n\nUse only standard Pydantic/FastAPI patterns."
        return base
    else:
        base = _build_detailed_props(prop_names, properties_by_name, max_show=20)
        if variant == 'aggressive':
            base += "\n\nUse advanced FastAPI patterns. async generators, websockets welcome."
        return base


# ============================================================================
# ADAPTIVE MODEL GENERATOR
# ============================================================================
async def _gen_module_model_llm_adaptive(
    module_name: str,
    properties: List,
    properties_by_name: dict,
    try_full_threshold: int,
    batch_size: int,
    prompt_variant: str
) -> Optional[str]:
    """
    Same as original _gen_module_model_llm but:
    - try_full_threshold comes from Thompson Sampling (was hardcoded 30)
    - batch_size         comes from Thompson Sampling (was hardcoded 15)
    - prompt style       comes from prompt selector
    """
    num_props  = len(properties)
    class_name = f"{module_name.capitalize()}Model"

    if num_props <= try_full_threshold:
        print(f"    → [ADAPTIVE] Full model generation (threshold={try_full_threshold}, props={num_props})")

        compact  = _build_adaptive_props(properties, properties_by_name, prompt_variant)
        detailed = _build_detailed_props(properties, properties_by_name, max_show=20)

        prompt = f"""Generate complete Pydantic model: {class_name}

Properties ({num_props}): {compact}

Detailed:
{detailed}

Generate:
- from pydantic import BaseModel
- class {class_name}(BaseModel) with all {num_props} fields
- Correct Python types (bool, int, float, str, Optional)
- Default values where appropriate

Output ONLY the Python code."""

        timeout = _timeout_manager.get_timeout("model", num_props, TIMEOUT_MODEL_SMALL)

        start  = time.time()
        result = await _call_async(prompt, timeout)
        duration = time.time() - start

        if result:
            _timeout_manager.record_success("model", num_props, duration)
            print(f"    ✓ Full model generated ({duration:.1f}s)")
            return result

        print(f"    ⚠ Full model timed out, trying progressive...")

    # Progressive with adaptive batch_size
    print(f"    → [ADAPTIVE] Progressive model (batch_size={batch_size}, props={num_props})")

    from agents.llm_backend_agent import ProgressiveGenerator
    generator = ProgressiveGenerator(batch_size=batch_size)
    result    = await generator.generate_model(module_name, properties, properties_by_name)

    if result:
        print(f"    ✓ Progressive model succeeded")
        return result

    return None


# ============================================================================
# ADAPTIVE SIMULATOR GENERATOR
# ============================================================================
async def _gen_module_simulator_llm_adaptive(
    module_name: str,
    properties: List,
    properties_by_name: dict,
    try_full_threshold: int,
    batch_size: int,
    prompt_variant: str
) -> Optional[str]:
    """
    Same as original _gen_module_simulator_llm but with adaptive constants
    """
    num_props = len(properties)
    func_name = f"simulate_{module_name.lower()}"

    if num_props <= try_full_threshold:
        print(f"    → [ADAPTIVE] Full simulator generation (threshold={try_full_threshold})")

        compact = _build_adaptive_props(properties, properties_by_name, prompt_variant)

        prompt = f"""Generate Python simulator function: {func_name}

Properties ({num_props}): {compact}

Generate:
- async def {func_name}() -> dict
- Returns dict with random realistic values for each property
- BOOLEAN: random.choice([True, False])
- INT: random.randint(appropriate_range)
- FLOAT: round(random.uniform(appropriate_range), 2)

Output ONLY the Python function."""

        timeout = _timeout_manager.get_timeout("simulator", num_props, TIMEOUT_SIMULATOR_SMALL)

        start  = time.time()
        result = await _call_async(prompt, timeout)
        duration = time.time() - start

        if result:
            _timeout_manager.record_success("simulator", num_props, duration)
            print(f"    ✓ Full simulator generated ({duration:.1f}s)")
            return result

        print(f"    ⚠ Full simulator timed out, trying progressive...")

    print(f"    → [ADAPTIVE] Progressive simulator (batch_size={batch_size})")

    from agents.llm_backend_agent import ProgressiveGenerator
    generator = ProgressiveGenerator(batch_size=batch_size)
    result    = await generator.generate_simulator(module_name, properties, properties_by_name)

    if result:
        print(f"    ✓ Progressive simulator succeeded")
        return result

    return None


# ============================================================================
# ADAPTIVE AGENT CLASS
# ============================================================================
class LLMBackendAgentAdaptive:
    """
    Adaptive version of LLMBackendAgent.

    What actually changes:
    - TRY_FULL_FIRST_THRESHOLD  learned by RL
    - PROGRESSIVE_BATCH_SIZE    learned by RL
    - prompt style              learned by prompt selector

    What stays the same:
    - file structure, output paths
    - template fallbacks
    - all FastAPI/Pydantic patterns
    - everything in llm_client.py
    """

    def __init__(self, output_root: str = "output"):
        self.writer   = SafeWriter(output_root)
        self.backend_dir = Path(output_root) / "backend" / "vss_dynamic_server"
        self.adaptive = get_adaptive_wrapper()
        self.stats = {
            "llm_success": 0,
            "llm_progressive": 0,
            "template_fallback": 0,
            "total": 0
        }

    def run(self, module_signal_map: dict, all_properties: list):
        prop_count = sum(len(v) for v in module_signal_map.values())

        decision = self.adaptive.decide_generation_strategy(
            properties=[{"name": f"p{i}"} for i in range(prop_count)],
            agent_name="BackendAgent"
        )

        chunk_size     = decision["chunk_size"]
        prompt_variant = decision["prompt_variant"]
        batch_size     = max(10, chunk_size - 5)
        try_full       = chunk_size

        print("[LLM BACKEND] Adaptive generation...")
        print(f"  [ADAPTIVE] chunk_size={chunk_size}, batch_size={batch_size}, "
              f"try_full_threshold={try_full}, prompt_variant={prompt_variant}")
        print(f"  Configuration:")
        print(f"    - Try full generation up to {try_full} properties (adaptive, was {TRY_FULL_FIRST_THRESHOLD})")
        print(f"    - Progressive batch size: {batch_size} (adaptive, was {PROGRESSIVE_BATCH_SIZE})")
        print(f"    - Prompt style: {prompt_variant} (adaptive)")
        print()

        start_time = time.time()

        try:
            asyncio.run(self._run_async(
                module_signal_map,
                list(all_properties) if not isinstance(all_properties, list) else all_properties,
                try_full, batch_size, prompt_variant
            ))
            success = True
            quality = self._compute_quality()
        except Exception as e:
            print(f"  [ADAPTIVE] Generation failed: {e}")
            success = False
            quality = 0.0

        elapsed = time.time() - start_time

        # Record result so RL can learn
        from adaptive_components.performance_tracker import GenerationRecord
        import time as _time
        self.adaptive.tracker.record_generation(GenerationRecord(
            timestamp=_time.time(),
            module_name="BackendAgent",
            property_count=prop_count,
            chunk_size=chunk_size,
            timeout=decision["timeout"],
            prompt_variant=prompt_variant,
            success=success,
            quality_score=quality,
            generation_time=elapsed,
            error_type=None,
            error_message=None,
            llm_model=self.adaptive.llm_model
        ))

        self.adaptive.chunk_optimizer.update_reward(
            chunk_size=chunk_size,
            success=success,
            quality_score=quality,
            generation_time=elapsed
        )
        self.adaptive.prompt_selector.update_performance(
            variant=prompt_variant,
            property_count=prop_count,
            success=success,
            quality_score=quality,
            generation_time=elapsed
        )
        self.adaptive._save_state()

    async def _run_async(self, module_signal_map, all_properties,
                         try_full, batch_size, prompt_variant):
        props_by_name = _index_properties(all_properties)
        modules       = sorted(module_signal_map.keys())

        # Static files (unchanged)
        print("  [WAVE A] Static files...")
        await self._generate_static_files()

        # Models (adaptive)
        print(f"\n  [WAVE B] Module models ({len(modules)} modules)...")
        await self._generate_models(modules, module_signal_map, props_by_name,
                                    try_full, batch_size, prompt_variant)

        # Simulators (adaptive)
        print(f"\n  [WAVE C] Module simulators ({len(modules)} modules)...")
        await self._generate_simulators(modules, module_signal_map, props_by_name,
                                        try_full, batch_size, prompt_variant)

        # main.py (unchanged)
        print("\n  [WAVE D] main.py...")
        await self._generate_main(modules)

        self._print_statistics()

    async def _generate_static_files(self):
        """Identical to original"""
        tasks = [
            ("requirements.txt", _gen_requirements_llm(), _get_template_requirements),
            ("config.py",        _gen_config_llm(),        _get_template_config),
        ]

        results = await asyncio.gather(*[t[1] for t in tasks])

        for i, (name, _, template_func) in enumerate(tasks):
            content = results[i]
            self.stats["total"] += 1

            if content:
                print(f"    ✓ {name}: LLM generated")
                self.stats["llm_success"] += 1
            else:
                print(f"    ⚠ {name}: Template")
                content = template_func()
                self.stats["template_fallback"] += 1

            path = self.backend_dir / name
            path.parent.mkdir(parents=True, exist_ok=True)
            self.writer.write(str(path), content.strip() + "\n")

    async def _generate_models(self, modules, module_signal_map, props_by_name,
                                try_full, batch_size, prompt_variant):
        tasks = [
            _gen_module_model_llm_adaptive(
                m, module_signal_map.get(m, []), props_by_name,
                try_full, batch_size, prompt_variant
            )
            for m in modules
        ]

        results = await asyncio.gather(*tasks)

        for i, module_name in enumerate(modules):
            content    = results[i]
            class_name = f"{module_name.capitalize()}Model"
            self.stats["total"] += 1

            if content:
                print(f"    ✓ {class_name}: LLM generated")
                self.stats["llm_success"] += 1
            else:
                print(f"    ⚠ {class_name}: Template")
                content = _get_template_model(module_name, module_signal_map.get(module_name, []),
                                              props_by_name)
                self.stats["template_fallback"] += 1

            path = self.backend_dir / f"models/{module_name.lower()}_model.py"
            path.parent.mkdir(parents=True, exist_ok=True)
            self.writer.write(str(path), content.strip() + "\n")

    async def _generate_simulators(self, modules, module_signal_map, props_by_name,
                                    try_full, batch_size, prompt_variant):
        tasks = [
            _gen_module_simulator_llm_adaptive(
                m, module_signal_map.get(m, []), props_by_name,
                try_full, batch_size, prompt_variant
            )
            for m in modules
        ]

        results = await asyncio.gather(*tasks)

        for i, module_name in enumerate(modules):
            content   = results[i]
            func_name = f"simulate_{module_name.lower()}"
            self.stats["total"] += 1

            if content:
                print(f"    ✓ {func_name}: LLM generated")
                self.stats["llm_success"] += 1
            else:
                print(f"    ⚠ {func_name}: Template")
                content = _get_template_simulator(module_name, module_signal_map.get(module_name, []),
                                                  props_by_name)
                self.stats["template_fallback"] += 1

            path = self.backend_dir / f"simulators/{module_name.lower()}_simulator.py"
            path.parent.mkdir(parents=True, exist_ok=True)
            self.writer.write(str(path), content.strip() + "\n")

    async def _generate_main(self, modules):
        """Identical to original"""
        self.stats["total"] += 1
        content = await _gen_main_llm(modules)

        if content:
            print(f"    ✓ main.py: LLM generated")
            self.stats["llm_success"] += 1
        else:
            print(f"    ⚠ main.py: Template")
            content = _get_template_main(modules)
            self.stats["template_fallback"] += 1

        path = self.backend_dir / "main.py"
        path.parent.mkdir(parents=True, exist_ok=True)
        self.writer.write(str(path), content.strip() + "\n")

    def _compute_quality(self) -> float:
        total = self.stats["total"]
        if total == 0:
            return 0.0
        return (self.stats["llm_success"] + self.stats["llm_progressive"]) / total

    def _print_statistics(self):
        total     = self.stats["total"]
        llm_total = self.stats["llm_success"] + self.stats["llm_progressive"]

        print(f"\n[LLM BACKEND] Generation complete!")
        print(f"  Total files: {total}")
        print(f"  LLM generated: {llm_total}/{total} ({100 * llm_total / total:.1f}%)")
        print(f"  Template fallback: {self.stats['template_fallback']}/{total}")

        if total > 0:
            rate = llm_total / total
            if rate >= 0.90:
                print(f"  ✓ Excellent! {rate:.1%} LLM generation rate")
            elif rate >= 0.80:
                print(f"  ✓ Good! {rate:.1%} LLM generation rate")
            else:
                print(f"  ⚠ {rate:.1%} — consider tuning timeouts")