import asyncio
import json
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from datetime import datetime
from llm_client import call_llm
from tools.safe_writer import SafeWriter

# Dedicated pool — 5 tasks fire in gather simultaneously.
# Using None (shared default pool) causes contention when multiple agents
# run concurrently from multi_main's Group A ThreadPoolExecutor.
_EXECUTOR = ThreadPoolExecutor(max_workers=5)

# TIMEOUT CONFIGURATION
# Reduced from 1800s (30min) to prevent pipeline blocking
LLM_TIMEOUT_PER_TASK = 900  # 2 minutes per diagram/document
TOTAL_TIMEOUT = 900  # 3 minutes for all tasks combined


# ---------------------------------------------------------------------------
# Shared context builder — assembled once, reused by every parallel task.
# Keeps individual prompts short without duplicating the module summary.
# ---------------------------------------------------------------------------
def _build_context(module_signal_map: dict, all_properties: list, full_spec_text: str) -> str:
    lines = []
    for module_name, prop_names in sorted(module_signal_map.items()):
        count = len(prop_names)
        if count == 0:
            lines.append(f"- {module_name}: (empty)")
            continue
        first_few = prop_names[:3]
        remaining = f" (+{count - 3} more)" if count > 3 else ""
        lines.append(f"- {module_name}: {count} properties ({', '.join(first_few)}{remaining})")

    # Duplicate-property warning (kept from original)
    all_names: set[str] = set()
    for names in module_signal_map.values():
        all_names.update(names)
    if len(all_names) < sum(len(v) for v in module_signal_map.values()):
        print("[DESIGN DOC] Warning: some property names appear in multiple modules")

    # OPTIMIZATION: Truncate spec even more aggressively to reduce prompt size
    return (
        f"Total VSS-derived properties: {len(all_properties)}\n"
        f"Logical modules: {len(module_signal_map)}\n"
        f"Modules and example properties:\n"
        + "\n".join(lines)
        + f"\n\nFull spec context (truncated):\n{full_spec_text[:2000]}"  # Reduced from 4000
    )


# ---------------------------------------------------------------------------
# Individual task definitions — each returns (filename, content | None).
# Tight, single-responsibility prompts keep output tokens low and focused.
# ---------------------------------------------------------------------------
SYSTEM_PROMPT = (
    "You are a senior automotive software architect specializing in "
    "Android Automotive OS Vehicle HAL. Output ONLY the requested artifact — "
    "no explanations, no extra text, no JSON wrapper unless explicitly asked. "
    "Be concise and focused."
)


async def _gen_architecture_diagram(context: str) -> tuple[str, str | None]:
    prompt = (
        f"{context}\n\n"
        "Generate a CONCISE PlantUML System Architecture Diagram showing: "
        "backend server → AIDL HAL → VehicleHalService → framework → app.\n"
        "Keep it simple with main components only.\n"
        "Output ONLY valid PlantUML between @startuml and @enduml."
    )
    raw = await _call_async(prompt, timeout=LLM_TIMEOUT_PER_TASK)
    return ("architecture.puml", _extract_plantuml(raw))


async def _gen_class_diagram(context: str) -> tuple[str, str | None]:
    prompt = (
        f"{context}\n\n"
        "Generate a CONCISE PlantUML Class Diagram for VehicleHalService.\n"
        "Include: VehicleHalService, key module handlers, AIDL interface.\n"
        "Focus on structure, not every detail.\n"
        "Output ONLY valid PlantUML between @startuml and @enduml."
    )
    raw = await _call_async(prompt, timeout=LLM_TIMEOUT_PER_TASK)
    return ("class_diagram.puml", _extract_plantuml(raw))


async def _gen_sequence_diagram(context: str) -> tuple[str, str | None]:
    prompt = (
        f"{context}\n\n"
        "Generate a CONCISE PlantUML Sequence Diagram for property get/set.\n"
        "Show: App → Framework → HAL → Backend flow.\n"
        "Keep it simple and clear.\n"
        "Output ONLY valid PlantUML between @startuml and @enduml."
    )
    raw = await _call_async(prompt, timeout=LLM_TIMEOUT_PER_TASK)
    return ("sequence_diagram.puml", _extract_plantuml(raw))


async def _gen_component_diagram(context: str) -> tuple[str, str | None]:
    prompt = (
        f"{context}\n\n"
        "Generate a CONCISE PlantUML Component Diagram showing main system components.\n"
        "Include: HAL, framework, app, backend, SELinux.\n"
        "Output ONLY valid PlantUML between @startuml and @enduml."
    )
    raw = await _call_async(prompt, timeout=LLM_TIMEOUT_PER_TASK)
    return ("component_diagram.puml", _extract_plantuml(raw))


async def _gen_design_document(context: str) -> tuple[str, str | None]:
    prompt = (
        f"{context}\n\n"
        "Write a CONCISE Design Document in Markdown (max 500 words) with:\n"
        "1. Overview — purpose and scope\n"
        "2. Architecture — layer breakdown\n"
        "3. Key Modules — what each module does\n"
        "4. Security — SELinux and permissions\n"
        "Be brief and technical. Output ONLY Markdown, no code fences around it."
    )
    raw = await _call_async(prompt, timeout=LLM_TIMEOUT_PER_TASK)
    return ("DESIGN_DOCUMENT.md", raw.strip() if raw else None)


# ---------------------------------------------------------------------------
# Template fallbacks — used if LLM fails or times out
# ---------------------------------------------------------------------------
def _get_template_architecture(module_signal_map: dict) -> str:
    """Fallback template for architecture diagram."""
    modules = '\n'.join([f'  [{m}Impl]' for m in sorted(module_signal_map.keys())])
    
    return f"""@startuml
!theme plain

component "Android Framework" as framework
component "Car Services" as carservice
component "Vehicle HAL Service" as halservice
{modules}
component "Vehicle ECU" as ecu

framework --> carservice
carservice --> halservice : AIDL
halservice --> ecu : CAN/Protocol

@enduml
"""


def _get_template_class_diagram(module_signal_map: dict) -> str:
    """Fallback template for class diagram."""
    modules = '\n'.join([f'class {m}Impl\nVehicleHalService --> {m}Impl' 
                         for m in sorted(module_signal_map.keys())])
    
    return f"""@startuml
!theme plain

interface IVehicle {{
  +getProperty()
  +setProperty()
}}

class VehicleHalService {{
  +start()
  +getProperty()
  +setProperty()
}}

{modules}

IVehicle <|.. VehicleHalService

@enduml
"""


def _get_template_sequence_diagram() -> str:
    """Fallback template for sequence diagram."""
    return """@startuml
!theme plain

App -> Framework: getProperty(id)
Framework -> CarService: getProperty(id)
CarService -> VehicleHAL: getProperty(id) [AIDL]
VehicleHAL -> ECU: read property
ECU --> VehicleHAL: value
VehicleHAL --> CarService: value
CarService --> Framework: value
Framework --> App: value

@enduml
"""


def _get_template_component_diagram(module_signal_map: dict) -> str:
    """Fallback template for component diagram."""
    return """@startuml
!theme plain

[AIDL Interface]
[HAL Service]
[Car Services]
[SELinux Policy]
[Vehicle ECU]

[Car Services] --> [AIDL Interface]
[AIDL Interface] --> [HAL Service]
[HAL Service] --> [Vehicle ECU]
[SELinux Policy] ..> [HAL Service] : enforces

@enduml
"""


def _get_template_design_doc(module_signal_map: dict, all_properties: list) -> str:
    """Fallback template for design document."""
    module_list = '\n'.join([f"- **{m}**: {len(props)} properties" 
                             for m, props in sorted(module_signal_map.items())])
    
    return f"""# Vehicle HAL Design Document

**Generated:** {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}

## Overview

This Vehicle HAL implements {len(module_signal_map)} functional modules with {len(all_properties)} properties for Android Automotive OS.

## Architecture

The system follows a layered architecture:
- **Android Framework**: System services and APIs
- **Car Services**: Framework integration layer
- **Vehicle HAL Service**: AIDL-based hardware abstraction
- **Module Implementations**: Domain-specific handlers
- **Vehicle ECU**: Hardware interface via CAN/protocol

## Modules

{module_list}

## Data Flow

1. Application calls CarPropertyManager
2. Framework routes to appropriate Car service
3. Car service invokes HAL via AIDL
4. HAL delegates to module implementation
5. Module communicates with ECU
6. Response flows back through layers

## Security

- SELinux policies enforce access control
- HAL runs as `hal_vehicle_default` domain
- Permissions validated at framework layer
- VINTF manifest declares interface

## Build & Testing

Build: `m android.hardware.automotive.vehicle-service`
Test: `atest VehicleHalServiceTest`
"""


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------
def _extract_plantuml(raw: str | None) -> str | None:
    """Pull @startuml...@enduml block out of whatever the LLM returned."""
    if not raw:
        return None
    start = raw.find("@startuml")
    end = raw.find("@enduml")
    if start == -1 or end == -1:
        return None  # malformed — caller will log and skip
    return raw[start : end + len("@enduml")]


async def _call_async(prompt: str, timeout: int = LLM_TIMEOUT_PER_TASK) -> str | None:
    """
    Run the (sync) call_llm in a thread with timeout protection.
    
    Args:
        prompt: LLM prompt
        timeout: Max seconds to wait
    
    Returns:
        LLM response or None on timeout/error
    """
    loop = asyncio.get_running_loop()
    try:
        # Wrap LLM call with asyncio timeout
        return await asyncio.wait_for(
            loop.run_in_executor(
                _EXECUTOR,
                lambda: call_llm(prompt=prompt, temperature=0.1, system=SYSTEM_PROMPT),
            ),
            timeout=timeout
        )
    except asyncio.TimeoutError:
        print(f"[DESIGN DOC] LLM call timed out after {timeout}s")
        return None
    except Exception as e:
        print(f"[DESIGN DOC] LLM call failed: {e}")
        return None


# ---------------------------------------------------------------------------
# Main agent class
# ---------------------------------------------------------------------------
class DesignDocAgent:
    def __init__(self, output_root: str = "output", use_llm: bool = True):
        """
        Initialize DesignDocAgent.
        
        Args:
            output_root: Base output directory
            use_llm: Whether to attempt LLM generation (if False, uses templates only)
        """
        self.writer = SafeWriter(output_root)
        self.doc_dir = Path("docs/design")
        self.doc_dir.mkdir(parents=True, exist_ok=True)
        self.use_llm = use_llm

    # ------------------------------------------------------------------
    # Public entry point — keeps the same signature as the original.
    # ------------------------------------------------------------------
    def run(self, module_signal_map: dict, all_properties: list, full_spec_text: str):
        """
        Generate design documents with timeout protection and template fallback.
        
        Strategy:
        1. Try LLM generation with aggressive timeout (3 min total)
        2. Fall back to templates for any failed tasks
        3. Always produce complete output
        """
        print("[DESIGN DOC] Generating design documents (parallel)...")
        asyncio.run(self._run_async(module_signal_map, all_properties, full_spec_text))

    # ------------------------------------------------------------------
    # Async core — fires all 5 tasks simultaneously with timeout.
    # ------------------------------------------------------------------
    async def _run_async(
        self, module_signal_map: dict, all_properties: list, full_spec_text: str
    ):
        context = _build_context(module_signal_map, all_properties, full_spec_text)

        if self.use_llm:
            # Try LLM generation with overall timeout
            try:
                print(f"[DESIGN DOC]   Attempting LLM generation ({TOTAL_TIMEOUT}s total timeout)...")
                
                results = await asyncio.wait_for(
                    asyncio.gather(
                        _gen_architecture_diagram(context),
                        _gen_class_diagram(context),
                        _gen_sequence_diagram(context),
                        _gen_component_diagram(context),
                        _gen_design_document(context),
                        return_exceptions=True,  # Don't fail if one task fails
                    ),
                    timeout=TOTAL_TIMEOUT
                )
                
                print("[DESIGN DOC]   LLM generation completed")
                
            except asyncio.TimeoutError:
                print(f"[DESIGN DOC]   ⚠ LLM generation timed out after {TOTAL_TIMEOUT}s")
                print("[DESIGN DOC]   → Falling back to templates for all files")
                results = [(None, None)] * 5  # Force template fallback
                
            except Exception as e:
                print(f"[DESIGN DOC]   ⚠ LLM generation failed: {e}")
                print("[DESIGN DOC]   → Falling back to templates")
                results = [(None, None)] * 5
        else:
            print("[DESIGN DOC]   Using templates (LLM disabled)")
            results = [(None, None)] * 5

        # Process results with template fallback
        file_configs = [
            ("architecture.puml", 0, lambda: _get_template_architecture(module_signal_map)),
            ("class_diagram.puml", 1, lambda: _get_template_class_diagram(module_signal_map)),
            ("sequence_diagram.puml", 2, lambda: _get_template_sequence_diagram()),
            ("component_diagram.puml", 3, lambda: _get_template_component_diagram(module_signal_map)),
            ("DESIGN_DOCUMENT.md", 4, lambda: _get_template_design_doc(module_signal_map, all_properties)),
        ]

        saved = 0
        for filename, idx, template_func in file_configs:
            # Get LLM result if available
            content = None
            if isinstance(results[idx], tuple):
                _, content = results[idx]
            
            # Fall back to template if LLM failed
            if not content:
                print(f"[DESIGN DOC]   Using template for {filename}")
                content = template_func()
            else:
                print(f"[DESIGN DOC]   Using LLM output for {filename}")

            # Write file
            if content:
                full_path = self.doc_dir / filename
                self.writer.write(str(full_path), content.rstrip() + "\n")
                saved += 1

        print(f"[DESIGN DOC] Done — {saved}/5 files generated in {self.doc_dir}/")
        
        if saved == 5:
            print(" ✓ All files generated successfully")
        else:
            print(f" ⚠ {5 - saved} files failed")
            
        print(" → Use PlantUML viewer (e.g. plantuml.com or VS Code plugin) for .puml files")
        print(" → DESIGN_DOCUMENT.md contains the full textual overview")