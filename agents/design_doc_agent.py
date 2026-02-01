import asyncio
from pathlib import Path
from llm_client import call_llm
from tools.safe_writer import SafeWriter


PACKAGE = "com.android.vssdynamic.app"
APP_DIR = Path("packages/apps/VssDynamicApp")

SYSTEM_PROMPT = (
    "You are an expert Android Automotive OS app developer writing AOSP code. "
    "Output ONLY the requested file — no explanations, no JSON wrapper, no markdown fences."
)


# ---------------------------------------------------------------------------
# Shared context builders
# ---------------------------------------------------------------------------
def _build_module_props_text(module_name: str, prop_names: list, properties_by_name: dict) -> str:
    """Property detail list scoped to one module — used by layout + fragment prompts."""
    lines = []
    for name in prop_names:
        prop = properties_by_name.get(name)
        if not prop:
            continue
        typ = getattr(prop, "type", "UNKNOWN")
        access = getattr(prop, "access", "READ")
        areas = getattr(prop, "areas", [])
        areas_str = f", areas={', '.join(areas)}" if areas else ""
        lines.append(f"- {name} ({typ}, {access}{areas_str})")
    return "\n".join(lines) if lines else "- (none)"


def _build_full_module_summary(module_signal_map: dict) -> str:
    lines = []
    for name, props in sorted(module_signal_map.items()):
        count = len(props)
        if count == 0:
            lines.append(f"- {name}: (empty)")
            continue
        first_few = props[:4]
        remaining = f" (+{count - 4} more)" if count > 4 else ""
        lines.append(f"- {name}: {count} properties ({', '.join(first_few)}{remaining})")
    return "\n".join(lines)


def _index_properties(all_properties: list) -> dict:
    """name/id → property object lookup. Warns on duplicates."""
    idx = {}
    for prop in all_properties:
        name = getattr(prop, "name", getattr(prop, "id", None))
        if not name:
            continue
        if name in idx:
            print(f"[LLM ANDROID APP] Warning: duplicate property name: {name}")
        idx[name] = prop
    return idx


# ---------------------------------------------------------------------------
# Wave A — static configs + per-module layouts (all independent)
# ---------------------------------------------------------------------------
async def _gen_manifest(module_summary: str) -> tuple[str, str | None]:
    prompt = (
        f"Package: {PACKAGE}\n"
        f"Modules:\n{module_summary}\n\n"
        "Generate AndroidManifest.xml for an AOSP Android Automotive app.\n"
        "Include all android.car.permission.CAR_* permissions needed to read/write vehicle properties.\n"
        "Register MainActivity as the launcher activity.\n"
        "Output ONLY the XML."
    )
    raw = await _call_async(prompt)
    return ("src/main/AndroidManifest.xml", raw)


async def _gen_android_bp() -> tuple[str, str | None]:
    prompt = (
        f"Generate an Android.bp build file for an AOSP app.\n"
        f"Package: {PACKAGE}\n"
        "App name: VssDynamicApp\n"
        "Dependencies: androidx.viewpager2, androidx.recyclerview, androidx.fragment, android.car.\n"
        "Output ONLY the .bp file content."
    )
    raw = await _call_async(prompt)
    return ("Android.bp", raw)


async def _gen_strings_xml(module_signal_map: dict) -> tuple[str, str | None]:
    module_names = sorted(module_signal_map.keys())
    prompt = (
        f"Generate res/values/strings.xml for an AOSP Android app.\n"
        f"App name: VssDynamicApp\n"
        f"Tab titles (one per module): {module_names}\n"
        "Output ONLY the XML."
    )
    raw = await _call_async(prompt)
    return ("src/main/res/values/strings.xml", raw)


async def _gen_activity_main_layout() -> tuple[str, str | None]:
    prompt = (
        "Generate res/layout/activity_main.xml for an Android app.\n"
        "Root: FrameLayout or LinearLayout.\n"
        "Contains: TabLayout (com.google.android.material.tabs.TabLayout) + ViewPager2.\n"
        "Output ONLY the XML."
    )
    raw = await _call_async(prompt)
    return ("src/main/res/layout/activity_main.xml", raw)


async def _gen_module_layout(module_name: str, props_text: str) -> tuple[str, str | None]:
    prompt = (
        f"Generate a layout XML for one tab in an Android Automotive app.\n"
        f"Module: {module_name}\n"
        f"Properties in this module:\n{props_text}\n\n"
        "UI rules:\n"
        "- BOOLEAN → Switch widget\n"
        "- INT / FLOAT → TextView for value display\n"
        "- READ_WRITE → add a Button labeled 'Set' next to the value\n"
        "- Wrap each property in a LinearLayout row with a label TextView.\n"
        "- Root: ScrollView → LinearLayout (vertical).\n"
        "Output ONLY the XML. File will be saved as layout_module_{module_name.lower()}.xml"
    )
    raw = await _call_async(prompt)
    filename = f"src/main/res/layout/layout_module_{module_name.lower()}.xml"
    return (filename, raw)


# ---------------------------------------------------------------------------
# Wave B — per-module Kotlin fragments (parallel across modules, but needs
#           layouts from Wave A to exist so the LLM knows the layout structure)
# ---------------------------------------------------------------------------
async def _gen_module_fragment(module_name: str, props_text: str) -> tuple[str, str | None]:
    layout_file = f"layout_module_{module_name.lower()}"
    class_name = f"{module_name.capitalize()}TabFragment"

    prompt = (
        f"Generate a Kotlin Fragment for an Android Automotive app tab.\n"
        f"Class name: {class_name}\n"
        f"Package: {PACKAGE}\n"
        f"Layout file: R.layout.{layout_file}\n"
        f"Module: {module_name}\n"
        f"Properties to wire up:\n{props_text}\n\n"
        "Requirements:\n"
        "- Extend Fragment.\n"
        "- In onCreateView, inflate the layout.\n"
        "- In onViewCreated, use CarPropertyManager.registerCallback() for each property.\n"
        "- Update UI on callback: Switch for BOOLEAN, setText for INT/FLOAT.\n"
        "- For READ_WRITE properties, wire the 'Set' Button to CarPropertyManager.setProperty().\n"
        "- Handle exceptions (log + show error text).\n"
        "- Do NOT import or reference any other module's fragment.\n"
        "Output ONLY the Kotlin source file."
    )
    raw = await _call_async(prompt)
    filename = (
        f"src/main/java/{PACKAGE.replace('.', '/')}/{class_name}.kt"
    )
    return (filename, raw)


# ---------------------------------------------------------------------------
# Wave C — MainActivity (single call, runs last; knows all fragment names)
# ---------------------------------------------------------------------------
async def _gen_main_activity(module_signal_map: dict) -> tuple[str, str | None]:
    # Build the fragment registration list so MainActivity knows exactly what to wire
    fragment_entries = []
    for module_name in sorted(module_signal_map.keys()):
        class_name = f"{module_name.capitalize()}TabFragment"
        fragment_entries.append(f"- Tab title: \"{module_name}\" → Fragment class: {class_name}")
    fragments_text = "\n".join(fragment_entries)

    prompt = (
        f"Generate MainActivity.kt for an Android Automotive app.\n"
        f"Package: {PACKAGE}\n"
        "Layout: R.layout.activity_main (TabLayout + ViewPager2).\n\n"
        f"Register these tabs in order:\n{fragments_text}\n\n"
        "Requirements:\n"
        "- Use a ViewPager2Adapter (inner class or separate) that returns each Fragment by position.\n"
        "- Bind TabLayout to ViewPager2 using TabLayoutMediator.\n"
        "- Each tab title comes from the list above.\n"
        "- Obtain CarPropertyManager via Car.createCar() in onCreate.\n"
        "- Pass CarPropertyManager to each fragment (via a companion object, ViewModel, or constructor arg).\n"
        "Output ONLY the Kotlin source file."
    )
    raw = await _call_async(prompt)
    filename = f"src/main/java/{PACKAGE.replace('.', '/')}/MainActivity.kt"
    return (filename, raw)


# ---------------------------------------------------------------------------
# Async helper — same pattern as DesignDocAgent
# ---------------------------------------------------------------------------
async def _call_async(prompt: str) -> str | None:
    loop = asyncio.get_running_loop()
    try:
        return await loop.run_in_executor(
            None,
            lambda: call_llm(prompt=prompt, temperature=0.0, system=SYSTEM_PROMPT),
        )
    except Exception as e:
        print(f"[LLM ANDROID APP] LLM call failed: {e}")
        return None


# ---------------------------------------------------------------------------
# Main agent class
# ---------------------------------------------------------------------------
class LLMAndroidAppAgent:
    def __init__(self, output_root: str = "output"):
        self.writer = SafeWriter(output_root)

    def run(self, module_signal_map: dict, all_properties: list):
        print("[LLM ANDROID APP] Generating dynamic Android app (parallel waves)...")
        APP_DIR.mkdir(parents=True, exist_ok=True)
        asyncio.run(self._run_async(module_signal_map, all_properties))

    async def _run_async(self, module_signal_map: dict, all_properties: list):
        props_by_name = _index_properties(all_properties)
        module_summary = _build_full_module_summary(module_signal_map)

        # Pre-compute per-module property text (pure data, no I/O)
        module_props_text: dict[str, str] = {}
        for module_name, prop_names in module_signal_map.items():
            module_props_text[module_name] = _build_module_props_text(
                module_name, prop_names, props_by_name
            )

        # ------------------------------------------------------------------
        # WAVE A — static configs + per-module layouts (all independent)
        # ------------------------------------------------------------------
        print("  [WAVE A] Static configs + module layouts...")
        wave_a_tasks = [
            _gen_manifest(module_summary),
            _gen_android_bp(),
            _gen_strings_xml(module_signal_map),
            _gen_activity_main_layout(),
        ]
        # One layout task per module
        for module_name in module_signal_map:
            wave_a_tasks.append(_gen_module_layout(module_name, module_props_text[module_name]))

        wave_a_results: list[tuple[str, str | None]] = await asyncio.gather(*wave_a_tasks)
        self._write_results(wave_a_results)

        # ------------------------------------------------------------------
        # WAVE B — per-module Kotlin fragments (parallel across modules)
        # ------------------------------------------------------------------
        print("  [WAVE B] Module fragments...")
        wave_b_tasks = [
            _gen_module_fragment(module_name, module_props_text[module_name])
            for module_name in module_signal_map
        ]
        wave_b_results: list[tuple[str, str | None]] = await asyncio.gather(*wave_b_tasks)
        self._write_results(wave_b_results)

        # ------------------------------------------------------------------
        # WAVE C — MainActivity (needs to know all fragment class names)
        # ------------------------------------------------------------------
        print("  [WAVE C] MainActivity...")
        filename, content = await _gen_main_activity(module_signal_map)
        self._write_results([(filename, content)])

        print("[LLM ANDROID APP] Done.")

    # ------------------------------------------------------------------
    # Write helper — shared across all waves
    # ------------------------------------------------------------------
    def _write_results(self, results: list[tuple[str, str | None]]):
        for filename, content in results:
            if not content:
                print(f"  [LLM ANDROID APP] Skipped {filename} — empty or failed")
                continue
            full_path = APP_DIR / filename
            full_path.parent.mkdir(parents=True, exist_ok=True)
            self.writer.write(str(full_path), content.strip() + "\n")
            print(f"  [LLM ANDROID APP] Wrote: {full_path}")