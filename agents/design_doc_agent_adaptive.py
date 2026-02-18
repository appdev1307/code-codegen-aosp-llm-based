# FILE: agents/design_doc_agent_adaptive.py
"""
Design Doc Agent - ADAPTIVE VERSION

What this actually changes vs original:
  - TIMEOUT_DIAGRAM   → learned by Thompson Sampling RL
  - TIMEOUT_DOCUMENT  → learned by Thompson Sampling RL
  - prompt style      → learned by prompt selector (concise vs detailed context)

Everything else is identical to design_doc_agent.py
"""

import asyncio
import time
from pathlib import Path
from typing import List, Optional

from llm_client import call_llm
from tools.safe_writer import SafeWriter

# ============================================================================
# ADAPTIVE IMPORTS
# ============================================================================
from adaptive_integration import get_adaptive_wrapper
import agents.design_doc_agent as _base

# ============================================================================
# COPY ALL CONSTANTS FROM ORIGINAL
# ============================================================================
SYSTEM_PROMPT    = _base.SYSTEM_PROMPT
_EXECUTOR        = _base._EXECUTOR

# These are what adaptive system controls:
TIMEOUT_DIAGRAM  = _base.TIMEOUT_DIAGRAM    # default 180s
TIMEOUT_DOCUMENT = _base.TIMEOUT_DOCUMENT   # default 240s

# Reuse all helpers from original
_timeout_manager        = _base._timeout_manager
_build_context          = _base._build_context
_call_async             = _base._call_async
_get_template_architecture = _base._get_template_architecture
_get_template_class     = _base._get_template_class
_get_template_sequence  = _base._get_template_sequence
_get_template_component = _base._get_template_component
_get_template_document  = _base._get_template_document


# ============================================================================
# ADAPTIVE CONTEXT BUILDER
# Replaces _build_context with variant-aware version
# ============================================================================
def _build_adaptive_context(module_signal_map: dict, properties: list,
                             yaml_spec: str, variant: str) -> str:
    """
    Select context detail level based on learned variant.

    variant='minimal'      → short context  (fewer tokens, faster)
    variant='detailed'     → full context   (same as original)
    variant='conservative' → short context  (safe patterns only)
    variant='aggressive'   → full context   (advanced diagrams)
    """
    base_context = _build_context(module_signal_map, properties, yaml_spec)

    if variant in ('minimal', 'conservative'):
        # Truncate to first half to reduce tokens
        lines = base_context.split('\n')
        half  = max(10, len(lines) // 2)
        short = '\n'.join(lines[:half])
        if variant == 'conservative':
            short += "\n\nUse simple, standard diagram patterns only."
        return short
    else:
        if variant == 'aggressive':
            return base_context + "\n\nUse advanced diagram features. Include detailed interactions."
        return base_context  # 'detailed' = full original context


# ============================================================================
# ADAPTIVE DIAGRAM GENERATORS
# Same prompts as original but with adaptive timeouts and context
# ============================================================================
async def _gen_architecture_adaptive(context: str, timeout: int) -> Optional[str]:
    """Same as original but uses adaptive timeout"""
    prompt = f"""{context}

Generate a Mermaid architecture diagram showing:
- HAL layer components
- AIDL interfaces
- Service connections
- Data flow

Output ONLY the Mermaid diagram code."""

    raw = await _call_async(prompt, timeout, "architecture")
    if raw and "graph" in raw.lower():
        return raw
    return None


async def _gen_class_adaptive(context: str, timeout: int) -> Optional[str]:
    """Same as original but uses adaptive timeout"""
    prompt = f"""{context}

Generate a Mermaid class diagram showing:
- HAL interface classes
- Data structures
- Relationships

Output ONLY the Mermaid diagram code."""

    raw = await _call_async(prompt, timeout, "class")
    if raw and ("classDiagram" in raw or "class " in raw):
        return raw
    return None


async def _gen_sequence_adaptive(context: str, timeout: int) -> Optional[str]:
    """Same as original but uses adaptive timeout"""
    prompt = f"""{context}

Generate a Mermaid sequence diagram showing:
- App → HAL → Hardware flow
- Property read/write sequence
- Callback mechanism

Output ONLY the Mermaid diagram code."""

    raw = await _call_async(prompt, timeout, "sequence")
    if raw and "sequenceDiagram" in raw:
        return raw
    return None


async def _gen_component_adaptive(context: str, timeout: int) -> Optional[str]:
    """Same as original but uses adaptive timeout"""
    prompt = f"""{context}

Generate a Mermaid component diagram showing:
- System components
- Dependencies
- Interfaces

Output ONLY the Mermaid diagram code."""

    raw = await _call_async(prompt, timeout, "component")
    if raw and ("graph" in raw.lower() or "flowchart" in raw.lower()):
        return raw
    return None


async def _gen_document_adaptive(context: str, timeout: int) -> Optional[str]:
    """Same as original but uses adaptive timeout"""
    prompt = f"""{context}

Generate a comprehensive HAL design document in Markdown:
- Overview
- Architecture
- API Reference
- Implementation Notes
- Integration Guide

Output ONLY the Markdown document."""

    raw = await _call_async(prompt, timeout, "document")
    if raw and len(raw) > 200:
        return raw
    return None


# ============================================================================
# ADAPTIVE AGENT CLASS
# ============================================================================
class DesignDocAgentAdaptive:
    """
    Adaptive version of DesignDocAgent.

    What actually changes:
    - TIMEOUT_DIAGRAM   learned by RL (was hardcoded 180s)
    - TIMEOUT_DOCUMENT  learned by RL (was hardcoded 240s)
    - context detail    learned by prompt selector

    What stays the same:
    - output file structure
    - template fallbacks
    - diagram types generated
    """

    def __init__(self, output_root: str = "output"):
        self.writer   = SafeWriter(output_root)
        self.doc_dir  = Path("docs/design")
        self.adaptive = get_adaptive_wrapper()
        self.stats = {
            "llm_success": 0,
            "template_fallback": 0,
            "total": 0
        }

    def run(self, module_signal_map: dict, properties: list, yaml_spec: str):
        prop_count = len(properties)

        decision = self.adaptive.decide_generation_strategy(
            properties=[{"name": f"p{i}"} for i in range(prop_count)],
            agent_name="DesignDocAgent"
        )

        prompt_variant = decision["prompt_variant"]

        # Adaptive timeouts: scale from learned base
        # RL chunk_size maps to timeout multiplier
        # bigger chunk = more complex = more time needed
        chunk_size        = decision["chunk_size"]
        timeout_multiplier = chunk_size / 20.0  # 20 is the default chunk

        adaptive_diagram_timeout  = int(TIMEOUT_DIAGRAM  * timeout_multiplier)
        adaptive_document_timeout = int(TIMEOUT_DOCUMENT * timeout_multiplier)

        # Clamp to reasonable range
        adaptive_diagram_timeout  = max(60,  min(adaptive_diagram_timeout,  600))
        adaptive_document_timeout = max(120, min(adaptive_document_timeout, 900))

        print("[DESIGN DOC] Adaptive generation...")
        print(f"  [ADAPTIVE] prompt_variant={prompt_variant}")
        print(f"  [ADAPTIVE] diagram_timeout={adaptive_diagram_timeout}s "
              f"(was {TIMEOUT_DIAGRAM}s), "
              f"document_timeout={adaptive_document_timeout}s "
              f"(was {TIMEOUT_DOCUMENT}s)")
        print(f"  Configuration:")
        print(f"    - Diagram timeout: {adaptive_diagram_timeout}s each (adaptive)")
        print(f"    - Document timeout: {adaptive_document_timeout}s (adaptive)")
        print(f"    - Context style: {prompt_variant} (adaptive)")
        print()

        start_time = time.time()

        try:
            asyncio.run(self._run_async(
                module_signal_map, properties, yaml_spec,
                adaptive_diagram_timeout, adaptive_document_timeout,
                prompt_variant
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
            module_name="DesignDocAgent",
            property_count=prop_count,
            chunk_size=chunk_size,
            timeout=adaptive_diagram_timeout,
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

    async def _run_async(self, module_signal_map, properties, yaml_spec,
                         diagram_timeout, document_timeout, prompt_variant):

        # Build context with adaptive detail level
        context = _build_adaptive_context(
            module_signal_map, properties, yaml_spec, prompt_variant
        )

        # Generate all diagrams in parallel (same as original)
        print("  [DIAGRAMS] Generating diagrams in parallel...")
        diagram_tasks = [
            ("architecture", _gen_architecture_adaptive(context, diagram_timeout)),
            ("class",        _gen_class_adaptive(context, diagram_timeout)),
            ("sequence",     _gen_sequence_adaptive(context, diagram_timeout)),
            ("component",    _gen_component_adaptive(context, diagram_timeout)),
        ]

        results = await asyncio.gather(*[t[1] for t in diagram_tasks])

        for i, (name, _) in enumerate(diagram_tasks):
            content = results[i]
            self.stats["total"] += 1

            if content:
                print(f"    ✓ {name} diagram: LLM generated")
                self.stats["llm_success"] += 1
            else:
                print(f"    ⚠ {name} diagram: Template")
                template_func = getattr(_base, f"_get_template_{name}")
                content = template_func()
                self.stats["template_fallback"] += 1

            path = self.doc_dir / f"{name}_diagram.md"
            path.parent.mkdir(parents=True, exist_ok=True)
            self.writer.write(str(path), content.strip() + "\n")

        # Generate design document
        print("  [DOCUMENT] Generating design document...")
        self.stats["total"] += 1
        doc = await _gen_document_adaptive(context, document_timeout)

        if doc:
            print(f"    ✓ Design document: LLM generated")
            self.stats["llm_success"] += 1
        else:
            print(f"    ⚠ Design document: Template")
            doc = _base._get_template_document()
            self.stats["template_fallback"] += 1

        path = self.doc_dir / "design_document.md"
        path.parent.mkdir(parents=True, exist_ok=True)
        self.writer.write(str(path), doc.strip() + "\n")

        self._print_statistics()

    def _compute_quality(self) -> float:
        total = self.stats["total"]
        if total == 0:
            return 0.0
        return self.stats["llm_success"] / total

    def _print_statistics(self):
        total     = self.stats["total"]
        llm_total = self.stats["llm_success"]

        print(f"\n[DESIGN DOC] Generation complete!")
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
                print(f"  ⚠ {rate:.1%} — consider increasing timeouts")