"""
LLM-First Design Doc Agent - Optimized for Maximum LLM Success
===============================================================

Goal: 90%+ LLM-generated documentation
Strategy: Compact prompts + generous timeouts

Key Features:
1. Compact context (2000 chars max)
2. Generous timeouts (180-240s per diagram)
3. Concise, focused prompts
4. High-quality template fallbacks
5. Adaptive timeout learning
"""

import asyncio
import time
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from datetime import datetime
from typing import Optional, List, Tuple
from llm_client import call_llm
from tools.safe_writer import SafeWriter

# Dedicated executor
_EXECUTOR = ThreadPoolExecutor(max_workers=5)

# ============================================================================
# TIMEOUT CONFIGURATION - LLM-First (Generous)
# ============================================================================
TIMEOUT_DIAGRAM = 180           # Per diagram (3 minutes)
TIMEOUT_DOCUMENT = 240          # Design document (4 minutes)
TOTAL_TIMEOUT = 1200            # Overall timeout (20 minutes for all 5 files)

ENABLE_ADAPTIVE_TIMEOUTS = True

SYSTEM_PROMPT = (
    "You are a senior automotive software architect. "
    "Generate complete, professional documentation. "
    "Output ONLY the requested content - no explanations, no extra text."
)


# ============================================================================
# Adaptive Timeout Manager
# ============================================================================
class AdaptiveTimeoutManager:
    def __init__(self):
        self.success_history = {}
    
    def get_timeout(self, task_type: str, base_timeout: int) -> int:
        if not ENABLE_ADAPTIVE_TIMEOUTS or task_type not in self.success_history:
            return base_timeout
        
        if len(self.success_history[task_type]) < 3:
            return base_timeout
        
        times = sorted(self.success_history[task_type])
        p90_index = int(len(times) * 0.9)
        p90_time = times[p90_index]
        optimized = int(p90_time * 1.2)
        
        return max(base_timeout // 2, min(optimized, base_timeout * 2))
    
    def record_success(self, task_type: str, duration: float):
        if task_type not in self.success_history:
            self.success_history[task_type] = []
        self.success_history[task_type].append(duration)
        if len(self.success_history[task_type]) > 20:
            self.success_history[task_type] = self.success_history[task_type][-20:]


_timeout_manager = AdaptiveTimeoutManager()


# ============================================================================
# Context Builder - STRATEGY 1: Compact Context
# ============================================================================
def _build_compact_context(module_signal_map: dict, all_properties: list,
                          full_spec_text: str) -> str:
    """
    Build ultra-compact context for prompts.
    Goal: <2000 characters
    """
    # Module summary (compact)
    module_lines = []
    for module_name, prop_names in sorted(module_signal_map.items()):
        count = len(prop_names)
        if count == 0:
            continue
        # Just count, no property names
        module_lines.append(f"- {module_name}: {count} properties")
    
    return (
        f"VSS-based Vehicle HAL System\n"
        f"Modules: {len(module_signal_map)}\n"
        f"Total properties: {len(all_properties)}\n\n"
        + "\n".join(module_lines)
        + f"\n\nSpec context: {full_spec_text[:1000]}"  # Very short excerpt
    )


# ============================================================================
# LLM Call - STRATEGY 2: Generous Timeouts
# ============================================================================
async def _call_async(prompt: str, timeout: int, task_type: str) -> Optional[str]:
    """Call LLM with timeout and tracking"""
    loop = asyncio.get_running_loop()
    start_time = time.time()
    
    try:
        result = await asyncio.wait_for(
            loop.run_in_executor(
                _EXECUTOR,
                lambda: call_llm(prompt=prompt, temperature=0.1, system=SYSTEM_PROMPT),
            ),
            timeout=timeout
        )
        
        duration = time.time() - start_time
        _timeout_manager.record_success(task_type, duration)
        return result
        
    except asyncio.TimeoutError:
        return None
    except Exception:
        return None


# ============================================================================
# LLM Generation - STRATEGY 3: Focused Prompts
# ============================================================================
async def _gen_architecture_diagram(context: str) -> Tuple[str, Optional[str]]:
    """Generate architecture diagram"""
    prompt = f"""{context}

Generate PlantUML System Architecture Diagram.

Show main layers:
- Android App
- Framework (Car Services)
- AIDL Interface
- HAL Service (with module implementations)
- Vehicle ECU

Keep it clean and professional.
Output ONLY @startuml...@enduml"""
    
    timeout = _timeout_manager.get_timeout("architecture", TIMEOUT_DIAGRAM)
    raw = await _call_async(prompt, timeout, "architecture")
    return ("architecture.puml", _extract_plantuml(raw))


async def _gen_class_diagram(context: str) -> Tuple[str, Optional[str]]:
    """Generate class diagram"""
    prompt = f"""{context}

Generate PlantUML Class Diagram for VehicleHalService.

Include:
- IVehicle interface
- VehicleHalService class
- Module implementation classes
- Key relationships

Output ONLY @startuml...@enduml"""
    
    timeout = _timeout_manager.get_timeout("class", TIMEOUT_DIAGRAM)
    raw = await _call_async(prompt, timeout, "class")
    return ("class_diagram.puml", _extract_plantuml(raw))


async def _gen_sequence_diagram(context: str) -> Tuple[str, Optional[str]]:
    """Generate sequence diagram"""
    prompt = f"""{context}

Generate PlantUML Sequence Diagram for property get/set flow.

Show interaction:
App → Framework → CarService → HAL → ECU

Include both get and set operations.
Output ONLY @startuml...@enduml"""
    
    timeout = _timeout_manager.get_timeout("sequence", TIMEOUT_DIAGRAM)
    raw = await _call_async(prompt, timeout, "sequence")
    return ("sequence_diagram.puml", _extract_plantuml(raw))


async def _gen_component_diagram(context: str) -> Tuple[str, Optional[str]]:
    """Generate component diagram"""
    prompt = f"""{context}

Generate PlantUML Component Diagram.

Show system components:
- AIDL Interface
- HAL Service
- Car Services
- SELinux Policy
- Vehicle ECU

Output ONLY @startuml...@enduml"""
    
    timeout = _timeout_manager.get_timeout("component", TIMEOUT_DIAGRAM)
    raw = await _call_async(prompt, timeout, "component")
    return ("component_diagram.puml", _extract_plantuml(raw))


async def _gen_design_document(context: str) -> Tuple[str, Optional[str]]:
    """Generate design document"""
    prompt = f"""{context}

Write comprehensive Design Document in Markdown.

Sections:
1. Overview - system purpose and scope
2. Architecture - layer breakdown and responsibilities
3. Modules - what each module handles
4. Data Flow - get/set property flows
5. Security - SELinux policies and permissions
6. Build & Testing - commands and procedures

Be technical and professional. ~800 words.
Output ONLY Markdown (no code fences around it)."""
    
    timeout = _timeout_manager.get_timeout("document", TIMEOUT_DOCUMENT)
    raw = await _call_async(prompt, timeout, "document")
    return ("DESIGN_DOCUMENT.md", raw.strip() if raw else None)


# ============================================================================
# Template Fallbacks - High Quality
# ============================================================================
def _get_template_architecture(module_signal_map: dict) -> str:
    """Production-quality architecture diagram template"""
    modules = '\n'.join([f'  component [{m}Impl]' for m in sorted(module_signal_map.keys())])
    
    return f"""@startuml
!theme plain
skinparam componentStyle rectangle

package "Android Automotive OS" {{
  component "Android App" as app
  component "Framework" as framework
  component "Car Services" as carservice
}}

package "Vehicle HAL" {{
  interface "AIDL Interface" as aidl
  component "HAL Service" as halservice
{modules}
}}

component "Vehicle ECU" as ecu

app --> framework
framework --> carservice
carservice --> aidl
aidl --> halservice
halservice --> ecu : CAN/Protocol

@enduml
"""


def _get_template_class_diagram(module_signal_map: dict) -> str:
    """Production-quality class diagram template"""
    modules = '\n'.join([
        f'class {m}Impl {{\n  +getProperty()\n  +setProperty()\n}}\n'
        f'VehicleHalService --> {m}Impl'
        for m in sorted(module_signal_map.keys())
    ])
    
    return f"""@startuml
!theme plain

interface IVehicle {{
  +getAllPropConfigs()
  +getPropConfigs(props)
  +getValues(requests)
  +setValues(requests)
  +subscribe(callback, props)
  +unsubscribe(callback, props)
}}

class VehicleHalService {{
  -mModules: Map
  +start()
  +getProperty(prop)
  +setProperty(prop, value)
  +registerCallback(callback)
}}

{modules}

IVehicle <|.. VehicleHalService

@enduml
"""


def _get_template_sequence_diagram() -> str:
    """Production-quality sequence diagram template"""
    return """@startuml
!theme plain

participant "App" as app
participant "Framework" as fw
participant "CarService" as cs
participant "VehicleHAL" as hal
participant "ECU" as ecu

== Get Property ==
app -> fw: getProperty(propId)
fw -> cs: getProperty(propId)
cs -> hal: getValues(request) [AIDL]
hal -> ecu: read property
ecu --> hal: value
hal --> cs: response
cs --> fw: value
fw --> app: value

== Set Property ==
app -> fw: setProperty(propId, value)
fw -> cs: setProperty(propId, value)
cs -> hal: setValues(request) [AIDL]
hal -> ecu: write property
ecu --> hal: ack
hal --> cs: status
cs --> fw: status
fw --> app: status

@enduml
"""


def _get_template_component_diagram(module_signal_map: dict) -> str:
    """Production-quality component diagram template"""
    return """@startuml
!theme plain
skinparam componentStyle rectangle

[Android App]
[Android Framework]
[Car Services]

package "Vehicle HAL" {
  [AIDL Interface]
  [HAL Service]
  [Module Implementations]
}

[SELinux Policy]
[Vehicle ECU]

[Android App] --> [Android Framework]
[Android Framework] --> [Car Services]
[Car Services] --> [AIDL Interface] : AIDL calls
[AIDL Interface] --> [HAL Service]
[HAL Service] --> [Module Implementations]
[Module Implementations] --> [Vehicle ECU] : CAN/Protocol
[SELinux Policy] ..> [HAL Service] : enforces

@enduml
"""


def _get_template_design_doc(module_signal_map: dict, all_properties: list) -> str:
    """Production-quality design document template"""
    module_list = '\n'.join([f"- **{m}**: {len(props)} properties ({', '.join(props[:3])}{'...' if len(props) > 3 else ''})"
                             for m, props in sorted(module_signal_map.items())])
    
    return f"""# Vehicle HAL Design Document

**Generated:** {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}  
**System:** Android Automotive OS Vehicle Hardware Abstraction Layer

## 1. Overview

This Vehicle HAL implements a comprehensive hardware abstraction layer for Android Automotive OS, providing standardized access to {len(all_properties)} vehicle properties across {len(module_signal_map)} functional modules. The implementation follows AOSP guidelines and uses the AIDL-based HAL architecture introduced in Android 13+.

## 2. Architecture

The system follows a layered architecture pattern:

### 2.1 Application Layer
Android applications use the `CarPropertyManager` API to interact with vehicle properties. This provides a high-level, type-safe interface for reading and writing vehicle data.

### 2.2 Framework Layer
The Android Automotive framework (`android.car`) handles:
- Permission validation
- Property subscription management
- Data type conversion and validation
- Client lifecycle management

### 2.3 Car Services Layer
`CarPropertyService` acts as the bridge between the framework and HAL:
- Routes property requests to appropriate HAL modules
- Manages callback registration and notification
- Implements caching and rate limiting
- Enforces access control policies

### 2.4 HAL Layer (AIDL)
The Vehicle HAL service implements the `IVehicle` AIDL interface:
- `getAllPropConfigs()`: Enumerate all supported properties
- `getValues()`: Read current property values
- `setValues()`: Update property values
- `subscribe()`: Register for property change notifications

### 2.5 Module Implementations
Each functional domain has a dedicated module implementation that handles the actual communication with vehicle ECUs via CAN bus, LIN, or other automotive protocols.

## 3. Modules

The system is organized into the following functional modules:

{module_list}

Each module:
- Implements domain-specific business logic
- Handles protocol encoding/decoding
- Manages ECU communication
- Validates property values and access permissions

## 4. Data Flow

### 4.1 Property Read Flow
1. Application calls `CarPropertyManager.getProperty(propertyId)`
2. Framework validates permissions and property access rights
3. `CarPropertyService` routes request to appropriate HAL module
4. HAL service calls module's `getProperty()` implementation
5. Module reads from ECU via CAN/protocol layer
6. Value propagates back through layers to application

### 4.2 Property Write Flow
1. Application calls `CarPropertyManager.setProperty(propertyId, value)`
2. Framework validates permissions and value constraints
3. `CarPropertyService` routes request to HAL
4. HAL service validates and forwards to module
5. Module writes to ECU
6. Acknowledgment propagates back to application

### 4.3 Property Subscription
1. Application registers callback via `CarPropertyManager.registerCallback()`
2. Framework registers with `CarPropertyService`
3. `CarPropertyService` subscribes via HAL AIDL interface
4. HAL monitors ECU for changes
5. Changes trigger callbacks up through the layers

## 5. Security

### 5.1 SELinux Policies
The HAL service runs in the `hal_vehicle_default` SELinux domain with restricted capabilities:
- Only vehicle-related system calls permitted
- Restricted file system access
- No network access
- Limited IPC capabilities

### 5.2 Permissions
Framework enforces Android permissions:
- `android.car.permission.CAR_INFO` - Read basic vehicle information
- `android.car.permission.CAR_DYNAMICS_STATE` - Read dynamic states
- `android.car.permission.CAR_ENERGY` - Read energy/fuel data
- `android.car.permission.CAR_POWERTRAIN` - Read/write powertrain
- `android.car.permission.CAR_VENDOR_EXTENSION` - Access vendor properties

### 5.3 VINTF Manifest
The HAL declares its interface in the VINTF manifest:
```xml
<hal format="aidl">
    <name>android.hardware.automotive.vehicle</name>
    <fqname>IVehicle/default</fqname>
</hal>
```

## 6. Build & Testing

### 6.1 Build Commands
```bash
# Build HAL service
m android.hardware.automotive.vehicle-service

# Build complete automotive package
m PRODUCT-aosp_car_x86_64-eng
```

### 6.2 Testing
```bash
# Unit tests
atest VehicleHalServiceTest

# Integration tests
atest VtsHalAutomotiveVehicleTargetTest

# Manual testing
adb shell dumpsys android.hardware.automotive.vehicle.IVehicle/default
```

### 6.3 Debugging
Enable verbose logging:
```bash
adb shell setprop log.tag.VehicleHalService VERBOSE
adb logcat | grep VehicleHal
```

## 7. Performance Considerations

- Property reads are cached with configurable TTL
- Subscription updates are batched to reduce IPC overhead
- CAN bus messages are aggregated when possible
- Memory-mapped buffers used for high-frequency properties

## 8. Future Enhancements

- Support for additional CAN protocols (CAN-FD, FlexRay)
- OTA update capability for HAL modules
- Enhanced diagnostic and debugging interfaces
- Cloud connectivity for vehicle data streaming
"""


# ============================================================================
# Helpers
# ============================================================================
def _extract_plantuml(raw: Optional[str]) -> Optional[str]:
    """Extract PlantUML block"""
    if not raw:
        return None
    start = raw.find("@startuml")
    end = raw.find("@enduml")
    if start == -1 or end == -1:
        return None
    return raw[start : end + len("@enduml")]


# ============================================================================
# Main Agent
# ============================================================================
class DesignDocAgent:
    """LLM-First Design Doc Agent"""
    
    def __init__(self, output_root: str = "output"):
        self.output_root = Path(output_root)
        self.writer = SafeWriter(output_root)
        # doc_dir must be under output_root so C3 writes to output_rag_dspy/
        # not a hardcoded ./docs/design/ relative to CWD
        self.doc_dir = self.output_root / "docs" / "design"
        self.doc_dir.mkdir(parents=True, exist_ok=True)
        self.stats = {
            "llm_success": 0,
            "template_fallback": 0,
            "total": 0
        }
    
    def run(self, module_signal_map: dict, all_properties: list, full_spec_text: str):
        print("[DESIGN DOC] LLM-First generation (optimized for quality)...")
        print(f"  Configuration:")
        print(f"    - Diagram timeout: {TIMEOUT_DIAGRAM}s each")
        print(f"    - Document timeout: {TIMEOUT_DOCUMENT}s")
        print(f"    - Total timeout: {TOTAL_TIMEOUT}s")
        print(f"    - Adaptive timeouts: {'enabled' if ENABLE_ADAPTIVE_TIMEOUTS else 'disabled'}")
        print()
        
        asyncio.run(self._run_async(module_signal_map, all_properties, full_spec_text))
    
    async def _run_async(self, module_signal_map: dict, all_properties: list, full_spec_text: str):
        context = _build_compact_context(module_signal_map, all_properties, full_spec_text)
        
        # Try LLM generation for all files
        try:
            print(f"  Generating all diagrams and documentation...")
            
            results = await asyncio.wait_for(
                asyncio.gather(
                    _gen_architecture_diagram(context),
                    _gen_class_diagram(context),
                    _gen_sequence_diagram(context),
                    _gen_component_diagram(context),
                    _gen_design_document(context),
                    return_exceptions=True,
                ),
                timeout=TOTAL_TIMEOUT
            )
            
        except asyncio.TimeoutError:
            print(f"  ⚠ Overall generation timed out after {TOTAL_TIMEOUT}s")
            results = [(None, None)] * 5
        except Exception as e:
            print(f"  ⚠ Generation failed: {e}")
            results = [(None, None)] * 5
        
        # Process results with template fallback
        file_configs = [
            ("architecture.puml", 0, lambda: _get_template_architecture(module_signal_map)),
            ("class_diagram.puml", 1, lambda: _get_template_class_diagram(module_signal_map)),
            ("sequence_diagram.puml", 2, lambda: _get_template_sequence_diagram()),
            ("component_diagram.puml", 3, lambda: _get_template_component_diagram(module_signal_map)),
            ("DESIGN_DOCUMENT.md", 4, lambda: _get_template_design_doc(module_signal_map, all_properties)),
        ]
        
        for filename, idx, template_func in file_configs:
            self.stats["total"] += 1
            
            # Get LLM result if available
            content = None
            if isinstance(results[idx], tuple):
                _, content = results[idx]
            
            # Use template if LLM failed
            if content:
                print(f"  ✓ {filename}: LLM generated")
                self.stats["llm_success"] += 1
            else:
                print(f"  ⚠ {filename}: Using template")
                content = template_func()
                self.stats["template_fallback"] += 1
            
            # Write file
            full_path = self.doc_dir / filename
            self.writer.write(str(full_path), content.rstrip() + "\n")
        
        # Print statistics
        self._print_statistics()
    
    def _print_statistics(self):
        total = self.stats["total"]
        llm = self.stats["llm_success"]
        
        print(f"\n[DESIGN DOC] Generation complete!")
        print(f"  Total files: {total}")
        print(f"  LLM generated: {llm}/{total} ({100 * llm / total:.1f}%)")
        print(f"  Template fallback: {self.stats['template_fallback']}/{total} ({100 * self.stats['template_fallback'] / total:.1f}%)")
        print(f"  Location: {self.doc_dir}/")
        print(f"  → Use PlantUML viewer for .puml files")
        print(f"  → See DESIGN_DOCUMENT.md for full overview")
        
        if llm / total >= 0.80:
            print(f"  ✓ Excellent! Above 80% LLM generation")
        else:
            print(f"  ⚠ Consider increasing timeouts for better quality")