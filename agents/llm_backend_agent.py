"""
LLM-First Backend Agent - Optimized for Maximum LLM Success
===========================================================

Goal: 90%+ LLM-generated code with production quality
Strategy: Compact prompts + generous timeouts + progressive generation

Key Features:
1. Compact property format (60 tokens vs 3000)
2. Generous timeouts (180-300s)
3. Try full generation before chunking
4. Progressive generation for large files
5. Adaptive timeout learning
6. Smart module handling
"""

import asyncio
import time
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from typing import List, Dict, Optional
from llm_client import call_llm
from tools.safe_writer import SafeWriter

# Dedicated executor
_EXECUTOR = ThreadPoolExecutor(max_workers=12)

BACKEND_DIR_TEMPLATE = "{output_root}/backend/vss_dynamic_server"

SYSTEM_PROMPT = (
    "You are an expert Python backend developer. "
    "Generate complete, production-ready code. "
    "Output ONLY the requested file content - no explanations, no markdown fences."
)

# ============================================================================
# TIMEOUT CONFIGURATION - LLM-First (Generous)
# ============================================================================
TIMEOUT_STATIC_FILE = 60        # requirements.txt, config
TIMEOUT_MODEL_SMALL = 150       # Models with ≤20 properties
TIMEOUT_MODEL_LARGE = 240       # Models with >20 properties
TIMEOUT_SIMULATOR_SMALL = 180   # Simulators with ≤20 properties
TIMEOUT_SIMULATOR_LARGE = 300   # Simulators with >20 properties
TIMEOUT_MAIN = 240              # main.py

# ============================================================================
# GENERATION CONFIGURATION
# ============================================================================
TRY_FULL_FIRST_THRESHOLD = 30   # Try full generation if ≤30 props
PROGRESSIVE_BATCH_SIZE = 15     # Batch size for progressive
ENABLE_ADAPTIVE_TIMEOUTS = True


# ============================================================================
# Adaptive Timeout Manager (shared with Android agent)
# ============================================================================
class AdaptiveTimeoutManager:
    def __init__(self):
        self.success_history: Dict[str, List[float]] = {}
    
    def get_timeout(self, task_type: str, num_items: int, base_timeout: int) -> int:
        if not ENABLE_ADAPTIVE_TIMEOUTS:
            return base_timeout
        
        key = f"{task_type}_{num_items//10*10}"
        if key not in self.success_history or len(self.success_history[key]) < 3:
            return base_timeout
        
        times = sorted(self.success_history[key])
        p90_index = int(len(times) * 0.9)
        p90_time = times[p90_index]
        optimized = int(p90_time * 1.2)
        
        return max(base_timeout // 2, min(optimized, base_timeout * 2))
    
    def record_success(self, task_type: str, num_items: int, duration: float):
        key = f"{task_type}_{num_items//10*10}"
        if key not in self.success_history:
            self.success_history[key] = []
        self.success_history[key].append(duration)
        if len(self.success_history[key]) > 20:
            self.success_history[key] = self.success_history[key][-20:]


_timeout_manager = AdaptiveTimeoutManager()


# ============================================================================
# Property Helpers - STRATEGY 1: Compact Prompts
# ============================================================================
def _build_compact_props(prop_names: List[str], properties_by_name: dict,
                         max_show: int = 5) -> str:
    """Ultra-compact: name|type|access"""
    samples = []
    for name in prop_names[:max_show]:
        prop = properties_by_name.get(name)
        if not prop:
            continue
        short_name = name.split('_')[-1] if '_' in name else name
        typ = getattr(prop, "type", "?")[:4].upper()
        samples.append(f"{short_name}|{typ}")
    
    if len(prop_names) > max_show:
        samples.append(f"...+{len(prop_names) - max_show} more")
    
    return ", ".join(samples)


def _build_detailed_props(prop_names: List[str], properties_by_name: dict,
                          max_show: int = 20) -> str:
    """More detailed but still compact"""
    lines = []
    for name in prop_names[:max_show]:
        prop = properties_by_name.get(name)
        if not prop:
            continue
        short_name = name.split('_')[-1] if '_' in name else name
        typ = getattr(prop, "type", "UNKNOWN")
        lines.append(f"- {short_name} ({typ})")
    
    if len(prop_names) > max_show:
        lines.append(f"... and {len(prop_names) - max_show} more")
    
    return "\n".join(lines)


def _index_properties(all_properties: list) -> dict:
    idx = {}
    for prop in all_properties:
        name = getattr(prop, "name", getattr(prop, "id", None))
        if name:
            idx[name] = prop
    return idx


# ============================================================================
# LLM Call - STRATEGY 2: Generous Timeouts
# ============================================================================
async def _call_async(prompt: str, timeout: int) -> Optional[str]:
    loop = asyncio.get_running_loop()
    start_time = time.time()
    
    try:
        result = await asyncio.wait_for(
            loop.run_in_executor(
                _EXECUTOR,
                lambda: call_llm(prompt=prompt, temperature=0.0, system=SYSTEM_PROMPT),
            ),
            timeout=timeout
        )
        return result
    except asyncio.TimeoutError:
        return None
    except Exception:
        return None


# ============================================================================
# Progressive Generator - STRATEGY 4
# ============================================================================
class ProgressiveGenerator:
    def __init__(self, batch_size: int = PROGRESSIVE_BATCH_SIZE):
        self.batch_size = batch_size
    
    async def generate_model(self, module_name: str, properties: List,
                            properties_by_name: dict) -> Optional[str]:
        """Generate Pydantic model progressively"""
        batches = [properties[i:i + self.batch_size]
                  for i in range(0, len(properties), self.batch_size)]
        
        print(f"      Progressive: {len(batches)} batches")
        
        class_name = f"{module_name.capitalize()}Model"
        
        # Generate field batches
        field_parts = []
        for batch_idx, batch in enumerate(batches):
            compact = _build_compact_props(batch, properties_by_name, max_show=len(batch))
            
            prompt = f"""Generate Pydantic model fields for batch {batch_idx + 1}/{len(batches)}.

Properties: {compact}

For EACH property generate field:
- BOOL → field_name: bool | None = None
- INT → field_name: int | None = None
- FLOA → field_name: float | None = None
- STRI → field_name: str | None = None

Use snake_case field names.
Output ONLY the field definitions."""
            
            result = await _call_async(prompt, 90)
            if result:
                field_parts.append(result)
        
        if not field_parts:
            return None
        
        # Combine
        fields = "\n    ".join(field_parts)
        return f'''from pydantic import BaseModel

class {class_name}(BaseModel):
    """Model for {module_name} module."""
    {fields}
'''
    
    async def generate_simulator(self, module_name: str, properties: List,
                                 properties_by_name: dict) -> Optional[str]:
        """Generate simulator progressively"""
        batches = [properties[i:i + self.batch_size]
                  for i in range(0, len(properties), self.batch_size)]
        
        print(f"      Progressive: {len(batches)} batches")
        
        func_name = f"simulate_{module_name.lower()}"
        
        # Generate value batches
        value_parts = []
        for batch_idx, batch in enumerate(batches):
            compact = _build_compact_props(batch, properties_by_name, max_show=len(batch))
            
            prompt = f"""Generate simulator values for batch {batch_idx + 1}/{len(batches)}.

Properties: {compact}

For EACH property generate:
- BOOL → "field": random.choice([True, False])
- INT → "field": random.randint(0, 100)
- FLOA → "field": round(random.uniform(0.0, 100.0), 2)
- STRI → "field": "value"

Output ONLY the dict entries (key: value pairs)."""
            
            result = await _call_async(prompt, 90)
            if result:
                value_parts.append(result)
        
        if not value_parts:
            return None
        
        # Combine
        values = ",\n        ".join(value_parts)
        return f'''import random

def {func_name}() -> dict:
    """Simulate {module_name} data."""
    return {{
        {values}
    }}
'''


# ============================================================================
# Templates (Last Resort)
# ============================================================================
def _get_template_requirements() -> str:
    return '''fastapi==0.104.1
uvicorn[standard]==0.24.0
websockets==12.0
pydantic==2.5.0
'''


def _get_template_config() -> str:
    return '''"""Configuration."""

class Config:
    HOST = "0.0.0.0"
    PORT = 8000
    UPDATE_INTERVAL = 1.0

config = Config()
'''


def _get_template_model(module_name: str, properties: List, properties_by_name: dict) -> str:
    class_name = f"{module_name.capitalize()}Model"
    fields = []
    
    for name in properties:
        prop = properties_by_name.get(name)
        if not prop:
            continue
        typ = getattr(prop, "type", "UNKNOWN")
        py_type = {"BOOLEAN": "bool", "INT": "int", "FLOAT": "float"}.get(typ, "str")
        field_name = name.lower().replace("vehicle_children_", "").replace("_children_", "_")
        fields.append(f"    {field_name}: {py_type} | None = None")
    
    fields_str = "\n".join(fields) if fields else "    pass"
    
    return f'''from pydantic import BaseModel

class {class_name}(BaseModel):
    """Model for {module_name}."""
{fields_str}
'''


def _get_template_simulator(module_name: str, properties: List, properties_by_name: dict) -> str:
    func_name = f"simulate_{module_name.lower()}"
    values = []
    
    for name in properties:
        prop = properties_by_name.get(name)
        if not prop:
            continue
        typ = getattr(prop, "type", "UNKNOWN")
        field_name = name.lower().replace("vehicle_children_", "").replace("_children_", "_")
        
        if typ == "BOOLEAN":
            val = "random.choice([True, False])"
        elif typ == "INT":
            val = "random.randint(0, 100)"
        elif typ == "FLOAT":
            val = "round(random.uniform(0.0, 100.0), 2)"
        else:
            val = '"unknown"'
        
        values.append(f'        "{field_name}": {val},')
    
    values_str = "\n".join(values) if values else '        "placeholder": 0,'
    
    return f'''import random

def {func_name}() -> dict:
    """Simulate {module_name}."""
    return {{
{values_str}
    }}
'''


def _get_template_main(modules: List[str]) -> str:
    imports = []
    routes = []
    
    for m in modules:
        model = f"{m.capitalize()}Model"
        sim = f"simulate_{m.lower()}"
        imports.append(f"from models_{m.lower()} import {model}")
        imports.append(f"from simulator_{m.lower()} import {sim}")
        routes.append(f'        "{m}": {sim}(),')
    
    imports_str = "\n".join(imports)
    routes_str = "\n".join(routes)
    
    return f'''from fastapi import FastAPI, WebSocket, WebSocketDisconnect
from fastapi.middleware.cors import CORSMiddleware
import asyncio

{imports_str}

app = FastAPI(title="VSS Dynamic Server")

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

@app.get("/health")
async def health():
    return {{"status": "ok"}}

@app.get("/api/data")
async def get_data():
    return {{
{routes_str}
    }}

@app.websocket("/ws/live")
async def websocket_live(websocket: WebSocket):
    await websocket.accept()
    try:
        while True:
            data = {{
{routes_str}
            }}
            await websocket.send_json(data)
            await asyncio.sleep(1.0)
    except WebSocketDisconnect:
        pass
'''


# ============================================================================
# LLM Generation - STRATEGY 1 & 3: Compact + Try Full First
# ============================================================================
async def _gen_requirements_llm() -> Optional[str]:
    prompt = """Generate requirements.txt for FastAPI backend.

Include: FastAPI, Uvicorn (with standard), websockets, Pydantic
Pin versions for reproducibility.

Output ONLY the file content."""
    
    return await _call_async(prompt, TIMEOUT_STATIC_FILE)


async def _gen_config_llm() -> Optional[str]:
    prompt = """Generate config.py for FastAPI server.

Include: HOST, PORT, UPDATE_INTERVAL settings as class Config.

Output ONLY Python code."""
    
    return await _call_async(prompt, TIMEOUT_STATIC_FILE)


async def _gen_model_llm(module_name: str, properties: List,
                         properties_by_name: dict) -> Optional[str]:
    """STRATEGY 3: Try full first"""
    num_props = len(properties)
    
    if num_props <= TRY_FULL_FIRST_THRESHOLD:
        # Try full generation
        print(f"    → Trying full model ({num_props} properties)")
        
        compact = _build_compact_props(properties, properties_by_name, max_show=5)
        detailed = _build_detailed_props(properties, properties_by_name, max_show=20)
        
        class_name = f"{module_name.capitalize()}Model"
        
        prompt = f"""Generate complete Pydantic model: {class_name}

Properties ({num_props}): {compact}

Detailed:
{detailed}

Generate complete model with:
- from pydantic import BaseModel
- Class {class_name}(BaseModel) with docstring
- All {num_props} fields as: field_name: type | None = None
- Type mapping: BOOLEAN→bool, INT→int, FLOAT→float, STRING→str
- Use snake_case field names

Output ONLY complete Python code."""
        
        timeout = _timeout_manager.get_timeout("model", num_props, TIMEOUT_MODEL_SMALL)
        
        start = time.time()
        result = await _call_async(prompt, timeout)
        duration = time.time() - start
        
        if result:
            _timeout_manager.record_success("model", num_props, duration)
            print(f"    ✓ Full model generated ({duration:.1f}s)")
            return result
        
        print(f"    ⚠ Full model timed out, trying progressive...")
    
    # STRATEGY 4: Progressive
    print(f"    → Progressive model generation")
    generator = ProgressiveGenerator()
    result = await generator.generate_model(module_name, properties, properties_by_name)
    
    if result:
        print(f"    ✓ Progressive model succeeded")
        return result
    
    return None


async def _gen_simulator_llm(module_name: str, properties: List,
                             properties_by_name: dict) -> Optional[str]:
    """STRATEGY 3: Try full first"""
    num_props = len(properties)
    
    if num_props <= TRY_FULL_FIRST_THRESHOLD:
        print(f"    → Trying full simulator ({num_props} properties)")
        
        compact = _build_compact_props(properties, properties_by_name, max_show=5)
        detailed = _build_detailed_props(properties, properties_by_name, max_show=20)
        
        func_name = f"simulate_{module_name.lower()}"
        
        prompt = f"""Generate complete simulator function: {func_name}

Properties ({num_props}): {compact}

Detailed:
{detailed}

Generate complete function:
- import random
- def {func_name}() -> dict with docstring
- Returns dict with all {num_props} properties
- Realistic random values:
  * BOOLEAN → random.choice([True, False])
  * INT → random.randint(0, 100)
  * FLOAT → round(random.uniform(0.0, 100.0), 2)
  * STRING → sensible default
- Use snake_case field names

Output ONLY complete Python code."""
        
        timeout = _timeout_manager.get_timeout("simulator", num_props, TIMEOUT_SIMULATOR_SMALL)
        
        start = time.time()
        result = await _call_async(prompt, timeout)
        duration = time.time() - start
        
        if result:
            _timeout_manager.record_success("simulator", num_props, duration)
            print(f"    ✓ Full simulator generated ({duration:.1f}s)")
            return result
        
        print(f"    ⚠ Full simulator timed out, trying progressive...")
    
    # Progressive
    print(f"    → Progressive simulator generation")
    generator = ProgressiveGenerator()
    result = await generator.generate_simulator(module_name, properties, properties_by_name)
    
    if result:
        print(f"    ✓ Progressive simulator succeeded")
        return result
    
    return None


async def _gen_main_llm(modules: List[str]) -> Optional[str]:
    """Generate main.py"""
    pairs = []
    for m in modules:
        pairs.append(f"- {m}: {m.capitalize()}Model / simulate_{m.lower()}()")
    
    pairs_text = "\n".join(pairs)
    
    prompt = f"""Generate main.py for FastAPI vehicle telemetry server.

Modules ({len(modules)}):
{pairs_text}

Generate complete main.py with:
- All model and simulator imports
- FastAPI app with CORS
- GET /health → {{"status": "ok"}}
- GET /api/data → dict with all module data
- WebSocket /ws/live → push data every 1 second
- Proper imports and error handling

Output ONLY complete Python code."""
    
    return await _call_async(prompt, TIMEOUT_MAIN)


# ============================================================================
# Main Agent
# ============================================================================
class LLMBackendAgent:
    """LLM-First Backend Agent - Optimized for quality"""
    
    def __init__(self, output_root: str = "output"):
        self.writer = SafeWriter(output_root)
        self.backend_dir = Path(output_root) / "backend" / "vss_dynamic_server"
        self.stats = {
            "llm_success": 0,
            "llm_progressive": 0,
            "template_fallback": 0,
            "total": 0
        }
    
    def run(self, module_signal_map: dict, all_properties: list):
        print("[LLM BACKEND] LLM-First generation (optimized for quality)...")
        print(f"  Configuration:")
        print(f"    - Try full generation up to {TRY_FULL_FIRST_THRESHOLD} properties")
        print(f"    - Progressive generation for larger modules")
        print(f"    - Adaptive timeouts: {'enabled' if ENABLE_ADAPTIVE_TIMEOUTS else 'disabled'}")
        print()
        
        self.backend_dir.mkdir(parents=True, exist_ok=True)
        asyncio.run(self._run_async(module_signal_map, all_properties))
    
    async def _run_async(self, module_signal_map: dict, all_properties: list):
        props_by_name = _index_properties(all_properties)
        modules = sorted(module_signal_map.keys())
        
        # WAVE A: Static + per-module models/simulators
        print("  [WAVE A] Static files + models/simulators...")
        await self._generate_wave_a(modules, module_signal_map, props_by_name)
        
        # WAVE B: main.py
        print("\n  [WAVE B] main.py...")
        await self._generate_main(modules)
        
        # Statistics
        self._print_statistics()
    
    async def _generate_wave_a(self, modules: List[str], module_signal_map: dict,
                               props_by_name: dict):
        """Generate static + per-module files"""
        tasks = []
        task_info = []
        
        # Static files
        tasks.append(_gen_requirements_llm())
        task_info.append(("requirements.txt", "static", lambda: _get_template_requirements()))
        
        tasks.append(_gen_config_llm())
        task_info.append(("config.py", "static", lambda: _get_template_config()))
        
        # Per-module models and simulators
        for module_name in modules:
            prop_names = module_signal_map.get(module_name, [])
            print(f"    {module_name}: {len(prop_names)} properties")
            
            # Model
            tasks.append(_gen_model_llm(module_name, prop_names, props_by_name))
            task_info.append((
                f"models_{module_name.lower()}.py",
                "model",
                lambda m=module_name, p=prop_names: _get_template_model(m, p, props_by_name)
            ))
            
            # Simulator
            tasks.append(_gen_simulator_llm(module_name, prop_names, props_by_name))
            task_info.append((
                f"simulator_{module_name.lower()}.py",
                "simulator",
                lambda m=module_name, p=prop_names: _get_template_simulator(m, p, props_by_name)
            ))
        
        # Execute all
        results = await asyncio.gather(*tasks)
        
        # Write files
        for i, (filename, ftype, template_func) in enumerate(task_info):
            content = results[i]
            self.stats["total"] += 1
            
            if content:
                if "Progressive" in str(content):
                    print(f"    ✓ {filename}: LLM progressive")
                    self.stats["llm_progressive"] += 1
                else:
                    print(f"    ✓ {filename}: LLM full")
                    self.stats["llm_success"] += 1
            else:
                print(f"    ⚠ {filename}: Template")
                content = template_func()
                self.stats["template_fallback"] += 1
            
            path = self.backend_dir / filename
            path.parent.mkdir(parents=True, exist_ok=True)
            self.writer.write(str(path), content.strip() + "\n")
    
    async def _generate_main(self, modules: List[str]):
        """Generate main.py"""
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
        self.writer.write(str(path), content.strip() + "\n")
    
    def _print_statistics(self):
        """Print statistics"""
        total = self.stats["total"]
        llm_total = self.stats["llm_success"] + self.stats["llm_progressive"]
        
        print(f"\n[LLM BACKEND] Generation complete!")
        print(f"  Total files: {total}")
        print(f"  LLM generated: {llm_total}/{total} ({100 * llm_total / total:.1f}%)")
        print(f"    - Full generation: {self.stats['llm_success']}")
        print(f"    - Progressive generation: {self.stats['llm_progressive']}")
        print(f"  Template fallback: {self.stats['template_fallback']}/{total} ({100 * self.stats['template_fallback'] / total:.1f}%)")
        print(f"  → Run: cd {self.backend_dir} && uvicorn main:app --reload")
        
        if llm_total / total >= 0.90:
            print(f"  ✓ Excellent! Achieved 90%+ LLM generation")
        elif llm_total / total >= 0.80:
            print(f"  ✓ Good! Above 80% LLM generation")
        else:
            print(f"  ⚠ Consider increasing timeouts")