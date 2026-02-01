import asyncio
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from llm_client import call_llm
from tools.safe_writer import SafeWriter

# Dedicated pool — Wave A fires 1 + 2*num_modules tasks simultaneously.
# 12 covers up to ~5 modules comfortably without hitting the shared default pool.
_EXECUTOR = ThreadPoolExecutor(max_workers=12)


BACKEND_DIR_TEMPLATE = "{output_root}/backend/vss_dynamic_server"

SYSTEM_PROMPT = (
    "You are an expert Python backend developer. "
    "Output ONLY the requested file — no explanations, no JSON wrapper, no markdown fences."
)


# ---------------------------------------------------------------------------
# Shared context helpers
# ---------------------------------------------------------------------------
def _props_for_module(module_name: str, prop_names: list, properties_by_name: dict) -> str:
    """Returns a concise property list scoped to one module."""
    lines = []
    for name in prop_names:
        prop = properties_by_name.get(name)
        if not prop:
            lines.append(f"- {name} (UNKNOWN)")
            continue
        typ = getattr(prop, "type", "UNKNOWN")
        lines.append(f"- {name} ({typ})")
    return "\n".join(lines) if lines else "- (none)"


def _index_properties(all_properties: list) -> dict:
    idx = {}
    for prop in all_properties:
        name = getattr(prop, "name", getattr(prop, "id", None))
        if not name:
            continue
        if name in idx:
            print(f"[LLM BACKEND] Warning: duplicate property name: {name}")
        idx[name] = prop
    return idx


# ---------------------------------------------------------------------------
# Wave A — all independent, fire together
# ---------------------------------------------------------------------------
async def _gen_requirements() -> tuple[str, str | None]:
    prompt = (
        "Generate a requirements.txt for a FastAPI backend that uses:\n"
        "- FastAPI\n"
        "- Uvicorn\n"
        "- WebSockets (the 'websockets' package)\n"
        "- Pydantic (comes with FastAPI, but pin it)\n"
        "Output ONLY the requirements.txt content. No comments."
    )
    raw = await _call_async(prompt)
    return ("requirements.txt", raw)


async def _gen_module_model(module_name: str, props_text: str) -> tuple[str, str | None]:
    """One Pydantic model class per module — scoped prompt, fast output."""
    prompt = (
        f"Generate a Pydantic model class for one vehicle telemetry module.\n"
        f"Module name: {module_name}\n"
        f"Properties:\n{props_text}\n\n"
        "Rules:\n"
        "- Class name: {class_name} (use CamelCase of module name + 'Model').\n"
        "- Each property becomes a field with an appropriate Python type:\n"
        "    BOOLEAN → bool, INT → int, FLOAT → float, STRING → str.\n"
        "- Use Optional[type] = None as default for every field.\n"
        "- Import from pydantic import BaseModel at the top.\n"
        "- Output ONLY the class definition (import + class). No other code."
    ).format(class_name=f"{module_name.capitalize()}Model")
    raw = await _call_async(prompt)
    return (f"models_{module_name.lower()}.py", raw)


async def _gen_module_simulator(module_name: str, props_text: str) -> tuple[str, str | None]:
    """One simulator function per module — produces realistic random data."""
    prompt = (
        f"Generate a Python function that simulates realistic vehicle telemetry for one module.\n"
        f"Module name: {module_name}\n"
        f"Properties:\n{props_text}\n\n"
        "Rules:\n"
        "- Function name: simulate_{name} (snake_case of module name).\n"
        "- Returns a dict with one key per property.\n"
        "- Use random values in realistic ranges:\n"
        "    BOOLEAN → random.choice([True, False])\n"
        "    INT     → random.randint with a plausible range for the property name\n"
        "    FLOAT   → round(random.uniform(...), 2) with a plausible range\n"
        "    STRING  → a short plausible string value\n"
        "- Import random at the top.\n"
        "- Output ONLY the import + function definition. No other code."
    ).format(name=module_name.lower())
    raw = await _call_async(prompt)
    return (f"simulator_{module_name.lower()}.py", raw)


# ---------------------------------------------------------------------------
# Wave B — main.py (single call, runs after Wave A)
# ---------------------------------------------------------------------------
async def _gen_main(module_signal_map: dict) -> tuple[str, str | None]:
    # Build the import + wiring manifest so the LLM knows exactly what exists
    import_lines = []
    model_classes = []
    sim_functions = []

    for module_name in sorted(module_signal_map.keys()):
        model_file = f"models_{module_name.lower()}"
        model_class = f"{module_name.capitalize()}Model"
        sim_file = f"simulator_{module_name.lower()}"
        sim_func = f"simulate_{module_name.lower()}"

        import_lines.append(f"from {model_file} import {model_class}")
        import_lines.append(f"from {sim_file} import {sim_func}")
        model_classes.append((module_name, model_class))
        sim_functions.append((module_name, sim_func))

    imports_text = "\n".join(import_lines)

    # Describe the routing structure
    route_entries = []
    for module_name, model_class, sim_func in zip(
        sorted(module_signal_map.keys()), 
        [mc for _, mc in model_classes],
        [sf for _, sf in sim_functions]
    ):
        route_entries.append(
            f"  \"{module_name}\": {sim_func}()  # validated against {model_class}"
        )
    routes_text = ",\n".join(route_entries)

    prompt = (
        "Generate main.py for a FastAPI backend serving live vehicle telemetry.\n\n"
        "These modules and imports already exist — use them exactly as shown:\n"
        f"{imports_text}\n\n"
        "Requirements:\n"
        "- FastAPI app instance.\n"
        "- GET /api/data → returns a JSON dict grouped by module. Build it like:\n"
        "  {\n"
        f"{routes_text}\n"
        "  }\n"
        "- WebSocket /ws/live → on connect, push the same grouped dict every 1 second\n"
        "  in a loop until the client disconnects.\n"
        "- Use asyncio.sleep(1) in the WebSocket loop.\n"
        "- On WebSocket disconnect, break the loop cleanly (catch WebSocketDisconnect).\n"
        "- Add a GET /health → {{ \"status\": \"ok\" }} endpoint.\n"
        "Output ONLY the main.py source. No other files."
    )
    raw = await _call_async(prompt)
    return ("main.py", raw)


# ---------------------------------------------------------------------------
# Async helper
# ---------------------------------------------------------------------------
async def _call_async(prompt: str) -> str | None:
    loop = asyncio.get_running_loop()
    try:
        return await loop.run_in_executor(
            _EXECUTOR,  # dedicated pool — avoids contention with other agents
            lambda: call_llm(prompt=prompt, temperature=0.0, system=SYSTEM_PROMPT),
        )
    except Exception as e:
        print(f"[LLM BACKEND] LLM call failed: {e}")
        return None


# ---------------------------------------------------------------------------
# Main agent class
# ---------------------------------------------------------------------------
class LLMBackendAgent:
    def __init__(self, output_root: str = "output"):
        self.writer = SafeWriter(output_root)
        self.backend_dir = Path(output_root) / "backend" / "vss_dynamic_server"

    def run(self, module_signal_map: dict, all_properties: list):
        print("[LLM BACKEND] Generating FastAPI backend (parallel waves)...")
        self.backend_dir.mkdir(parents=True, exist_ok=True)
        asyncio.run(self._run_async(module_signal_map, all_properties))

    async def _run_async(self, module_signal_map: dict, all_properties: list):
        props_by_name = _index_properties(all_properties)

        # Pre-compute per-module property text (pure data, no I/O)
        module_props_text: dict[str, str] = {}
        for module_name, prop_names in module_signal_map.items():
            module_props_text[module_name] = _props_for_module(
                module_name, prop_names, props_by_name
            )

        # --------------------------------------------------------------
        # WAVE A — requirements.txt + per-module models + per-module sims
        # All independent, all fire at the same time.
        # --------------------------------------------------------------
        print("  [WAVE A] requirements.txt + module models + module simulators...")
        wave_a_tasks = [_gen_requirements()]

        for module_name in module_signal_map:
            text = module_props_text[module_name]
            wave_a_tasks.append(_gen_module_model(module_name, text))
            wave_a_tasks.append(_gen_module_simulator(module_name, text))

        wave_a_results: list[tuple[str, str | None]] = await asyncio.gather(*wave_a_tasks)
        self._write_results(wave_a_results)

        # --------------------------------------------------------------
        # WAVE B — main.py (knows all model + simulator file/class names)
        # --------------------------------------------------------------
        print("  [WAVE B] main.py...")
        filename, content = await _gen_main(module_signal_map)
        self._write_results([(filename, content)])

        print("[LLM BACKEND] Done.")
        print(f"  → Run: cd {self.backend_dir} && uvicorn main:app --reload")

    # ------------------------------------------------------------------
    def _write_results(self, results: list[tuple[str, str | None]]):
        for filename, content in results:
            if not content:
                print(f"  [LLM BACKEND] Skipped {filename} — empty or failed")
                continue
            full_path = self.backend_dir / filename
            full_path.parent.mkdir(parents=True, exist_ok=True)
            self.writer.write(str(full_path), content.strip() + "\n")
            print(f"  [LLM BACKEND] Wrote: {full_path}")