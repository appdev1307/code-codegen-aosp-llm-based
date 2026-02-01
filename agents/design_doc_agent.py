import asyncio
import json
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from llm_client import call_llm
from tools.safe_writer import SafeWriter

# Dedicated pool — 5 tasks fire in gather simultaneously.
# Using None (shared default pool) causes contention when multiple agents
# run concurrently from multi_main's Group A ThreadPoolExecutor.
_EXECUTOR = ThreadPoolExecutor(max_workers=5)


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

    return (
        f"Total VSS-derived properties: {len(all_properties)}\n"
        f"Logical modules: {len(module_signal_map)}\n"
        f"Modules and example properties:\n"
        + "\n".join(lines)
        + f"\n\nFull spec context (truncated):\n{full_spec_text[:4000]}"
    )


# ---------------------------------------------------------------------------
# Individual task definitions — each returns (filename, content | None).
# Tight, single-responsibility prompts keep output tokens low and focused.
# ---------------------------------------------------------------------------
SYSTEM_PROMPT = (
    "You are a senior automotive software architect specializing in "
    "Android Automotive OS Vehicle HAL. Output ONLY the requested artifact — "
    "no explanations, no extra text, no JSON wrapper unless explicitly asked."
)


async def _gen_architecture_diagram(context: str) -> tuple[str, str | None]:
    prompt = (
        f"{context}\n\n"
        "Generate a PlantUML System Architecture Diagram that shows the full "
        "end-to-end system: backend telemetry server → AIDL HAL interface → "
        "C++ NDK VehicleHalService → Android framework → client app.\n"
        "Include the major VSS modules as grouped components inside VehicleHalService.\n"
        "Output ONLY valid PlantUML between @startuml and @enduml."
    )
    raw = await _call_async(prompt)
    return ("architecture.puml", _extract_plantuml(raw))


async def _gen_class_diagram(context: str) -> tuple[str, str | None]:
    prompt = (
        f"{context}\n\n"
        "Generate a PlantUML Class Diagram for VehicleHalService and its related classes.\n"
        "Include: VehicleHalService, PropertyAccessor, ModuleHandler (one per major module), "
        "CallbackRegistry, and the AIDL service interface.\n"
        "Show fields, key methods, and relationships (inheritance, dependency, association).\n"
        "Output ONLY valid PlantUML between @startuml and @enduml."
    )
    raw = await _call_async(prompt)
    return ("class_diagram.puml", _extract_plantuml(raw))


async def _gen_sequence_diagram(context: str) -> tuple[str, str | None]:
    prompt = (
        f"{context}\n\n"
        "Generate a PlantUML Sequence Diagram showing the full property get/set flow "
        "with callback registration.\n"
        "Participants: Client App, Android Framework, AIDL Stub, VehicleHalService, "
        "PropertyAccessor, CallbackRegistry, Backend.\n"
        "Cover three scenarios in order: (1) GET property, (2) SET property, "
        "(3) register change callback + notification.\n"
        "Output ONLY valid PlantUML between @startuml and @enduml."
    )
    raw = await _call_async(prompt)
    return ("sequence_diagram.puml", _extract_plantuml(raw))


async def _gen_component_diagram(context: str) -> tuple[str, str | None]:
    prompt = (
        f"{context}\n\n"
        "Generate a PlantUML Component Diagram showing: HAL layer, VehicleHalService, "
        "Android framework layer, SELinux policy enforcement, client app, and backend server.\n"
        "Highlight which SELinux domains govern access between components.\n"
        "Output ONLY valid PlantUML between @startuml and @enduml."
    )
    raw = await _call_async(prompt)
    return ("component_diagram.puml", _extract_plantuml(raw))


async def _gen_design_document(context: str) -> tuple[str, str | None]:
    prompt = (
        f"{context}\n\n"
        "Write a high-level Design Document in Markdown with these sections:\n"
        "1. Overview — what this system does and why it exists\n"
        "2. Architecture — layer breakdown and component responsibilities\n"
        "3. Modules & Properties — what each module owns, reference the actual module names\n"
        "4. Data Flow & Callbacks — how get/set and change notifications work end-to-end\n"
        "5. Security Considerations — SELinux contexts, access control, threat model\n"
        "Output ONLY the Markdown. No JSON, no code fences around the whole thing."
    )
    raw = await _call_async(prompt)
    return ("DESIGN_DOCUMENT.md", raw.strip() if raw else None)


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


async def _call_async(prompt: str) -> str | None:
    """Run the (sync) call_llm in a thread so asyncio.gather can parallelize."""
    loop = asyncio.get_running_loop()
    try:
        return await loop.run_in_executor(
            _EXECUTOR,  # dedicated pool — avoids contention with other agents
            lambda: call_llm(prompt=prompt, temperature=0.1, system=SYSTEM_PROMPT),
        )
    except Exception as e:
        print(f"[DESIGN DOC] LLM call failed: {e}")
        return None


# ---------------------------------------------------------------------------
# Main agent class
# ---------------------------------------------------------------------------
class DesignDocAgent:
    def __init__(self, output_root: str = "output"):
        self.writer = SafeWriter(output_root)
        self.doc_dir = Path("docs/design")
        self.doc_dir.mkdir(parents=True, exist_ok=True)

    # ------------------------------------------------------------------
    # Public entry point — keeps the same signature as the original.
    # ------------------------------------------------------------------
    def run(self, module_signal_map: dict, all_properties: list, full_spec_text: str):
        print("[DESIGN DOC] Generating design documents (parallel)...")
        asyncio.run(self._run_async(module_signal_map, all_properties, full_spec_text))

    # ------------------------------------------------------------------
    # Async core — fires all 5 tasks simultaneously.
    # ------------------------------------------------------------------
    async def _run_async(
        self, module_signal_map: dict, all_properties: list, full_spec_text: str
    ):
        context = _build_context(module_signal_map, all_properties, full_spec_text)

        # Fire all 5 generations in parallel
        results: list[tuple[str, str | None]] = await asyncio.gather(
            _gen_architecture_diagram(context),
            _gen_class_diagram(context),
            _gen_sequence_diagram(context),
            _gen_component_diagram(context),
            _gen_design_document(context),
            return_exceptions=False,
        )

        # Write whatever succeeded
        saved = 0
        for filename, content in results:
            if not content:
                print(f"[DESIGN DOC] Skipped {filename} — LLM returned empty or malformed output")
                continue

            full_path = self.doc_dir / filename
            self.writer.write(str(full_path), content.rstrip() + "\n")
            saved += 1
            print(f"[DESIGN DOC] Wrote: {full_path}")

        print(f"[DESIGN DOC] Done — {saved}/5 files generated in {self.doc_dir}/")
        if saved < 5:
            print(" → Some files failed. Check logs above for details.")
        print(" → Use PlantUML viewer (e.g. plantuml.com or VS Code plugin) for .puml files")
        print(" → DESIGN_DOCUMENT.md contains the full textual overview")