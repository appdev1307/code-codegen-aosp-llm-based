# FILE: agents/llm_android_app_agent_adaptive.py
"""
Android App Agent - ADAPTIVE VERSION

What this actually changes vs original:
  - MAX_PROPS_PER_CHUNK      → learned by Thompson Sampling RL
  - PROGRESSIVE_BATCH_SIZE   → learned by Thompson Sampling RL
  - TRY_FULL_FIRST_THRESHOLD → learned by Thompson Sampling RL
  - prompt style             → learned by prompt selector (compact vs detailed)
  - timeouts                 → already adaptive inside original agent

Everything else is identical to llm_android_app_agent.py
"""

import asyncio
import time
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from typing import List, Dict, Optional, Tuple
from dataclasses import dataclass
from llm_client import call_llm
from tools.safe_writer import SafeWriter

# ============================================================================
# ADAPTIVE IMPORTS
# ============================================================================
from adaptive_integration import get_adaptive_wrapper
import agents.llm_android_app_agent as _base

# ============================================================================
# COPY ALL CONSTANTS FROM ORIGINAL (these become adaptive defaults)
# ============================================================================
PACKAGE           = _base.PACKAGE
APP_DIR           = _base.APP_DIR
SYSTEM_PROMPT     = _base.SYSTEM_PROMPT
_EXECUTOR         = _base._EXECUTOR

TIMEOUT_STATIC_FILE       = _base.TIMEOUT_STATIC_FILE
TIMEOUT_LAYOUT_SMALL      = _base.TIMEOUT_LAYOUT_SMALL
TIMEOUT_LAYOUT_LARGE      = _base.TIMEOUT_LAYOUT_LARGE
TIMEOUT_FRAGMENT_SMALL    = _base.TIMEOUT_FRAGMENT_SMALL
TIMEOUT_FRAGMENT_LARGE    = _base.TIMEOUT_FRAGMENT_LARGE
TIMEOUT_MAIN_ACTIVITY     = _base.TIMEOUT_MAIN_ACTIVITY
TIMEOUT_PROGRESSIVE_BATCH = _base.TIMEOUT_PROGRESSIVE_BATCH

# These three are what the adaptive system actually controls:
TRY_FULL_FIRST_THRESHOLD  = _base.TRY_FULL_FIRST_THRESHOLD   # default 30
MAX_PROPS_PER_CHUNK       = _base.MAX_PROPS_PER_CHUNK         # default 20
PROGRESSIVE_BATCH_SIZE    = _base.PROGRESSIVE_BATCH_SIZE      # default 15

ENABLE_ADAPTIVE_TIMEOUTS  = _base.ENABLE_ADAPTIVE_TIMEOUTS
TIMEOUT_BUFFER_PERCENT    = _base.TIMEOUT_BUFFER_PERCENT

# Reuse all helpers from original
_timeout_manager            = _base._timeout_manager
_build_compact_props        = _base._build_compact_props
_build_detailed_props       = _base._build_detailed_props
_index_properties           = _base._index_properties
_call_async                 = _base._call_async
_gen_manifest_llm           = _base._gen_manifest_llm
_gen_android_bp_llm         = _base._gen_android_bp_llm
_gen_strings_llm            = _base._gen_strings_llm
_gen_activity_main_layout_llm = _base._gen_activity_main_layout_llm
_gen_main_activity_llm      = _base._gen_main_activity_llm
_get_template_manifest      = _base._get_template_manifest
_get_template_android_bp    = _base._get_template_android_bp
_get_template_strings       = _base._get_template_strings
_get_template_activity_main = _base._get_template_activity_main
_get_template_module_layout = _base._get_template_module_layout
_get_template_module_fragment = _base._get_template_module_fragment
_get_template_main_activity = _base._get_template_main_activity


# ============================================================================
# ADAPTIVE PROMPT BUILDERS
# Replaces _build_compact_props with variant-aware version
# ============================================================================
def _build_adaptive_props(prop_names: List[str], properties_by_name: dict,
                          variant: str) -> str:
    """
    Select prompt detail level based on learned variant.

    variant='minimal'     → ultra compact  (same as _build_compact_props)
    variant='detailed'    → more context   (same as _build_detailed_props)
    variant='conservative'→ compact + safety note
    variant='aggressive'  → detailed + advanced patterns
    """
    if variant in ('minimal', 'conservative'):
        base = _build_compact_props(prop_names, properties_by_name, max_show=5)
        if variant == 'conservative':
            base += "\n\nUse only standard Android patterns. No experimental APIs."
        return base
    else:
        base = _build_detailed_props(prop_names, properties_by_name, max_show=20)
        if variant == 'aggressive':
            base += "\n\nUse modern Kotlin patterns. Coroutines, Flow, StateFlow welcome."
        return base


# ============================================================================
# ADAPTIVE LAYOUT GENERATOR
# Same logic as original _gen_module_layout_llm but uses adaptive constants
# ============================================================================
async def _gen_module_layout_llm_adaptive(
    module_name: str,
    properties: List,
    properties_by_name: dict,
    try_full_threshold: int,
    max_chunk: int,
    batch_size: int,
    prompt_variant: str
) -> Optional[str]:
    """
    Identical to original _gen_module_layout_llm but:
    - try_full_threshold  comes from Thompson Sampling (was hardcoded 30)
    - batch_size          comes from Thompson Sampling (was hardcoded 15)
    - prompt style        comes from prompt selector   (was always compact)
    """
    num_props = len(properties)

    if num_props <= try_full_threshold:
        print(f"    → [ADAPTIVE] Full generation (threshold={try_full_threshold}, props={num_props})")

        compact = _build_adaptive_props(properties, properties_by_name, prompt_variant)

        prompt = f"""Generate complete layout XML for {module_name} with {num_props} properties.

Sample: {compact}

For EACH of the {num_props} properties, generate appropriate widget:
- BOOLEAN: <Switch android:id="@+id/switch_{{name}}" android:text="{{Label}}" />
- INT/FLOAT: <TextView android:id="@+id/text_{{name}}" android:text="{{Label}}: --" />

Root: ScrollView > LinearLayout (vertical, padding 16dp)

Generate complete XML with ALL {num_props} properties."""

        timeout = _timeout_manager.get_timeout("layout", num_props, TIMEOUT_LAYOUT_SMALL)

        start = time.time()
        result = await _call_async(prompt, timeout)
        duration = time.time() - start

        if result:
            _timeout_manager.record_success("layout", num_props, duration)
            print(f"    ✓ Full generation succeeded ({duration:.1f}s)")
            return result

        print(f"    ⚠ Full generation timed out, trying progressive...")

    # Progressive generation with ADAPTIVE batch_size
    print(f"    → [ADAPTIVE] Progressive generation (batch_size={batch_size}, props={num_props})")

    from agents.llm_android_app_agent import ProgressiveGenerator
    generator = ProgressiveGenerator(batch_size=batch_size)
    result = await generator.generate_layout(module_name, properties, properties_by_name)

    if result:
        print(f"    ✓ Progressive generation succeeded")
        return result

    return None


# ============================================================================
# ADAPTIVE FRAGMENT GENERATOR
# Same as original but uses adaptive constants
# ============================================================================
async def _gen_module_fragment_llm_adaptive(
    module_name: str,
    properties: List,
    properties_by_name: dict,
    chunk_id: int,
    try_full_threshold: int,
    batch_size: int,
    prompt_variant: str
) -> Optional[str]:
    """
    Identical to original _gen_module_fragment_llm but:
    - try_full_threshold comes from Thompson Sampling
    - batch_size         comes from Thompson Sampling
    - prompt style       comes from prompt selector
    """
    num_props = len(properties)

    class_name  = f"{module_name.capitalize()}Fragment" + (f"Part{chunk_id}" if chunk_id > 0 else "")
    layout_name = f"layout_module_{module_name.lower()}" + (f"_part{chunk_id}" if chunk_id > 0 else "")

    if num_props <= try_full_threshold:
        print(f"    → [ADAPTIVE] Full fragment generation (threshold={try_full_threshold})")

        compact  = _build_adaptive_props(properties, properties_by_name, prompt_variant)
        detailed = _build_detailed_props(properties, properties_by_name, max_show=20)

        prompt = f"""Generate complete Kotlin Fragment: {class_name}

Package: {PACKAGE}
Layout: R.layout.{layout_name}
Properties ({num_props}): {compact}

Detailed properties:
{detailed}

Generate complete Fragment with:
- Package and imports
- Class extending Fragment
- onCreateView (inflate layout)
- onViewCreated:
  * findViewById for each widget
  * CarPropertyManager.registerCallback for each property
  * Update UI in callbacks (Switch/TextView)
  * Error handling

Output ONLY complete Kotlin code."""

        timeout = _timeout_manager.get_timeout("fragment", num_props, TIMEOUT_FRAGMENT_SMALL)

        start = time.time()
        result = await _call_async(prompt, timeout)
        duration = time.time() - start

        if result:
            _timeout_manager.record_success("fragment", num_props, duration)
            print(f"    ✓ Full fragment generated ({duration:.1f}s)")
            return result

        print(f"    ⚠ Full fragment timed out, trying progressive...")

    print(f"    → [ADAPTIVE] Progressive fragment (batch_size={batch_size})")

    from agents.llm_android_app_agent import ProgressiveGenerator
    generator = ProgressiveGenerator(batch_size=batch_size)
    result = await generator.generate_fragment(module_name, properties, properties_by_name, chunk_id)

    if result:
        print(f"    ✓ Progressive fragment succeeded")
        return result

    return None


# ============================================================================
# ADAPTIVE AGENT CLASS
# ============================================================================
class LLMAndroidAppAgentAdaptive:
    """
    Adaptive version of LLMAndroidAppAgent.

    What actually changes:
    - chunk size (MAX_PROPS_PER_CHUNK)    learned by RL
    - batch size (PROGRESSIVE_BATCH_SIZE) learned by RL
    - full-gen threshold                  learned by RL
    - prompt style                        learned by prompt selector

    What stays the same:
    - file structure, output paths
    - template fallbacks
    - wave-based parallel execution
    - everything in llm_client.py
    """

    def __init__(self, output_root: str = "output"):
        self.writer   = _base.SafeWriter(output_root)
        self.adaptive = get_adaptive_wrapper()
        self.stats = {
            "llm_success": 0,
            "llm_progressive": 0,
            "template_fallback": 0,
            "total": 0
        }

    def run(self, module_signal_map: dict, all_properties: list):
        # Ask adaptive wrapper: what chunk size and prompt variant to use?
        all_props_list = list(all_properties) if not isinstance(all_properties, list) else all_properties
        prop_count     = sum(len(v) for v in module_signal_map.values())

        decision = self.adaptive.decide_generation_strategy(
            properties=[{"name": f"p{i}"} for i in range(prop_count)],
            agent_name="AndroidAppAgent"
        )

        # Extract the adaptive decisions
        chunk_size      = decision["chunk_size"]       # RL-learned
        prompt_variant  = decision["prompt_variant"]   # selector-learned
        # Use chunk_size for both max_chunk and batch (they're related)
        batch_size      = max(10, chunk_size - 5)      # batch slightly smaller than chunk
        # Threshold: if RL says chunk=20, try full up to 20
        try_full        = chunk_size

        print("[LLM ANDROID APP] Adaptive generation...")
        print(f"  [ADAPTIVE] chunk_size={chunk_size}, batch_size={batch_size}, "
              f"try_full_threshold={try_full}, prompt_variant={prompt_variant}")
        print(f"  Configuration:")
        print(f"    - Try full generation up to {try_full} properties (adaptive, was {TRY_FULL_FIRST_THRESHOLD})")
        print(f"    - Progressive batch size: {batch_size} (adaptive, was {PROGRESSIVE_BATCH_SIZE})")
        print(f"    - Prompt style: {prompt_variant} (adaptive)")
        print(f"    - Adaptive timeouts: {'enabled' if ENABLE_ADAPTIVE_TIMEOUTS else 'disabled'}")
        print()

        start_time = time.time()

        try:
            asyncio.run(self._run_async(
                module_signal_map, all_props_list,
                try_full, chunk_size, batch_size, prompt_variant
            ))
            success   = True
            quality   = self._compute_quality()
        except Exception as e:
            print(f"  [ADAPTIVE] Generation failed: {e}")
            success   = False
            quality   = 0.0

        elapsed = time.time() - start_time

        # Record result so RL can learn
        from adaptive_components.performance_tracker import GenerationRecord
        import time as _time
        record = _base.SafeWriter  # just need the import line below
        from adaptive_components.performance_tracker import GenerationRecord as GR
        self.adaptive.tracker.record_generation(GR(
            timestamp=_time.time(),
            module_name="AndroidAppAgent",
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

        # Update RL with actual result
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

    async def _run_async(self, module_signal_map: dict, all_properties: list,
                         try_full: int, max_chunk: int, batch_size: int,
                         prompt_variant: str):
        props_by_name = _index_properties(all_properties)
        modules       = sorted(module_signal_map.keys())

        # WAVE A: Static configs (unchanged from original)
        print("  [WAVE A] Static configuration files...")
        await self._generate_static_files(modules)

        # WAVE B: Layouts — NOW uses adaptive chunk/batch/threshold
        print(f"\n  [WAVE B] Module layouts ({len(modules)} modules)...")
        layout_info = await self._generate_layouts(
            modules, module_signal_map, props_by_name,
            try_full, max_chunk, batch_size, prompt_variant
        )

        # WAVE C: Fragments — NOW uses adaptive chunk/batch/threshold
        print(f"\n  [WAVE C] Module fragments ({len(layout_info)} fragments)...")
        await self._generate_fragments(
            layout_info, props_by_name,
            try_full, batch_size, prompt_variant
        )

        # WAVE D: MainActivity (unchanged from original)
        print("\n  [WAVE D] MainActivity...")
        await self._generate_main_activity(modules)

        self._print_statistics()

    async def _generate_static_files(self, modules):
        """Identical to original — static files don't need adaptation"""
        tasks = [
            ("AndroidManifest.xml", _gen_manifest_llm(modules),          lambda: _get_template_manifest(modules)),
            ("Android.bp",          _gen_android_bp_llm(),                _get_template_android_bp),
            ("strings.xml",         _gen_strings_llm(modules),            lambda: _get_template_strings(modules)),
            ("activity_main.xml",   _gen_activity_main_layout_llm(),      _get_template_activity_main),
        ]

        results = await asyncio.gather(*[t[1] for t in tasks])

        for i, (name, _, template_func) in enumerate(tasks):
            content = results[i]
            self.stats["total"] += 1

            if content:
                print(f"    ✓ {name}: LLM generated")
                self.stats["llm_success"] += 1
            else:
                print(f"    ⚠ {name}: Using template")
                content = template_func()
                self.stats["template_fallback"] += 1

            if name == "AndroidManifest.xml":
                path = APP_DIR / "src/main/AndroidManifest.xml"
            elif name == "Android.bp":
                path = APP_DIR / "Android.bp"
            elif name == "strings.xml":
                path = APP_DIR / "src/main/res/values/strings.xml"
            else:
                path = APP_DIR / "src/main/res/layout/activity_main.xml"

            path.parent.mkdir(parents=True, exist_ok=True)
            self.writer.write(str(path), content.strip() + "\n")

    async def _generate_layouts(self, modules, module_signal_map, props_by_name,
                                 try_full, max_chunk, batch_size, prompt_variant):
        """Same as original but passes adaptive constants to generator"""
        layout_tasks = []
        layout_info  = []

        for module_name in modules:
            prop_names = module_signal_map.get(module_name, [])
            num_props  = len(prop_names)

            print(f"    {module_name}: {num_props} properties")

            if num_props > max_chunk and num_props > try_full:
                num_chunks = (num_props + max_chunk - 1) // max_chunk
                print(f"      → [ADAPTIVE] Chunking into {num_chunks} parts (chunk={max_chunk})")

                for chunk_id in range(num_chunks):
                    chunk = prop_names[chunk_id * max_chunk : (chunk_id + 1) * max_chunk]
                    layout_tasks.append(_gen_module_layout_llm_adaptive(
                        module_name, chunk, props_by_name,
                        try_full, max_chunk, batch_size, prompt_variant
                    ))
                    layout_info.append((module_name, chunk_id, chunk))
            else:
                layout_tasks.append(_gen_module_layout_llm_adaptive(
                    module_name, prop_names, props_by_name,
                    try_full, max_chunk, batch_size, prompt_variant
                ))
                layout_info.append((module_name, 0, prop_names))

        results = await asyncio.gather(*layout_tasks)

        for i, (module_name, chunk_id, prop_names) in enumerate(layout_info):
            content      = results[i]
            chunk_suffix = f"_part{chunk_id}" if chunk_id > 0 else ""
            filename     = f"layout_module_{module_name.lower()}{chunk_suffix}.xml"
            self.stats["total"] += 1

            if content:
                print(f"    ✓ {filename}: LLM generated")
                self.stats["llm_success"] += 1
            else:
                print(f"    ⚠ {filename}: Template")
                content = _get_template_module_layout(module_name, prop_names, props_by_name)
                self.stats["template_fallback"] += 1

            path = APP_DIR / "src/main/res/layout" / filename
            path.parent.mkdir(parents=True, exist_ok=True)
            self.writer.write(str(path), content.strip() + "\n")

        return layout_info

    async def _generate_fragments(self, layout_info, props_by_name,
                                   try_full, batch_size, prompt_variant):
        """Same as original but passes adaptive constants to generator"""
        fragment_tasks = [
            _gen_module_fragment_llm_adaptive(
                module_name, prop_names, props_by_name, chunk_id,
                try_full, batch_size, prompt_variant
            )
            for module_name, chunk_id, prop_names in layout_info
        ]

        results = await asyncio.gather(*fragment_tasks)

        for i, (module_name, chunk_id, prop_names) in enumerate(layout_info):
            content    = results[i]
            class_name = f"{module_name.capitalize()}Fragment" + (f"Part{chunk_id}" if chunk_id > 0 else "")
            self.stats["total"] += 1

            if content:
                print(f"    ✓ {class_name}.kt: LLM generated")
                self.stats["llm_success"] += 1
            else:
                print(f"    ⚠ {class_name}.kt: Template")
                content = _get_template_module_fragment(module_name, chunk_id)
                self.stats["template_fallback"] += 1

            path = APP_DIR / f"src/main/java/{PACKAGE.replace('.', '/')}/{class_name}.kt"
            path.parent.mkdir(parents=True, exist_ok=True)
            self.writer.write(str(path), content.strip() + "\n")

    async def _generate_main_activity(self, modules):
        """Identical to original"""
        self.stats["total"] += 1
        content = await _gen_main_activity_llm(modules)

        if content:
            print(f"    ✓ MainActivity.kt: LLM generated")
            self.stats["llm_success"] += 1
        else:
            print(f"    ⚠ MainActivity.kt: Template")
            content = _get_template_main_activity(modules)
            self.stats["template_fallback"] += 1

        path = APP_DIR / f"src/main/java/{PACKAGE.replace('.', '/')}/MainActivity.kt"
        path.parent.mkdir(parents=True, exist_ok=True)
        self.writer.write(str(path), content.strip() + "\n")

    def _compute_quality(self) -> float:
        total = self.stats["total"]
        if total == 0:
            return 0.0
        llm = self.stats["llm_success"] + self.stats["llm_progressive"]
        return llm / total

    def _print_statistics(self):
        total     = self.stats["total"]
        llm_total = self.stats["llm_success"] + self.stats["llm_progressive"]

        print(f"\n[LLM ANDROID APP] Generation complete!")
        print(f"  Total files: {total}")
        print(f"  LLM generated: {llm_total}/{total} ({100 * llm_total / total:.1f}%)")
        print(f"    - Full generation: {self.stats['llm_success']}")
        print(f"    - Progressive:     {self.stats['llm_progressive']}")
        print(f"  Template fallback: {self.stats['template_fallback']}/{total}")

        if total > 0:
            rate = llm_total / total
            if rate >= 0.90:
                print(f"  ✓ Excellent! {rate:.1%} LLM generation rate")
            elif rate >= 0.80:
                print(f"  ✓ Good! {rate:.1%} LLM generation rate")
            else:
                print(f"  ⚠ {rate:.1%} — consider tuning timeouts")