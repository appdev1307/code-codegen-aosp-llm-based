"""
dspy_opt/hal_signatures.py
═══════════════════════════════════════════════════════════════════
DSPy Signature definitions — one per generation agent.

A DSPy Signature is a formal contract that specifies:
  - InputField(s):  what the LLM receives
  - OutputField(s): what the LLM must produce
  - Docstring:      task instruction (the part MIPROv2 optimises)

MIPROv2 rewrites the docstring and selects few-shot demonstrations
automatically, guided by the metric functions in metrics.py.

Signatures are grouped by pipeline layer:
  A. HAL Layer      — AIDL, C++, SELinux, Android.bp, VINTF
  B. Design Layer   — Design document, PlantUML diagrams
  C. App Layer      — Android Kotlin fragments + XML layouts
  D. Backend Layer  — FastAPI server, Pydantic models, Simulator
═══════════════════════════════════════════════════════════════════
"""

"""
dspy_opt/hal_signatures.py
═══════════════════════════════════════════════════════════════════
DSPy Signature definitions — one per generation agent.

A DSPy Signature is a formal contract that specifies:
  - InputField(s):  what the LLM receives
  - OutputField(s): what the LLM must produce
  - Docstring:      task instruction (the part MIPROv2 optimises)

MIPROv2 rewrites the docstring and selects few-shot demonstrations
automatically, guided by the metric functions in metrics.py.

Signatures are grouped by pipeline layer:
  A. HAL Layer      — AIDL, C++, SELinux, Android.bp, VINTF
  B. Design Layer   — Design document, PlantUML diagrams
  C. App Layer      — Android Kotlin fragments + XML layouts
  D. Backend Layer  — FastAPI server, Pydantic models, Simulator
═══════════════════════════════════════════════════════════════════
"""

import dspy


# ═══════════════════════════════════════════════════════════════════
# A. HAL LAYER  (5 signatures)
# ═══════════════════════════════════════════════════════════════════

class AIDLSignature(dspy.Signature):
    """
    Generate a complete, syntactically valid Android 14 AIDL property enum
    file for a VHAL HAL module. This file defines property ID constants
    (NOT a service interface).

    The output MUST be an @Backing(type="int") enum following the pattern
    of VehicleProperty.aidl in AOSP. The enum name MUST match the filename
    (e.g. VehiclePropertyAdas for VehiclePropertyAdas.aidl).

    Requirements:
    - Package: android.hardware.automotive.vehicle (NO .V2_0, NO .adas)
    - Use @VintfStability and @Backing(type="int") annotations
    - Declare an ENUM (e.g. 'enum VehiclePropertyAdas'), NOT an interface
    - Each property is an enum constant with a hex ID value
    - CRITICAL: Use domain-specific base address from aosp_context (e.g. 0x1000 for ADAS,
      0x2000 for BODY, 0x3000 for CABIN, 0x4000 for CHASSIS, 0x5000 for HVAC,
      0x6000 for INFOTAINMENT, 0x7000 for POWERTRAIN) for globally unique IDs
    - Include comment with type, access mode, and area for each property
    - DO NOT generate getter/setter methods, 'oneway', 'out' params, or 'throws'
    - DO NOT generate 'interface' — only 'enum'
    - Follow the retrieved AOSP VehicleProperty.aidl examples

    Example output (ADAS domain, base=0x1000):
    package android.hardware.automotive.vehicle;
    @VintfStability
    @Backing(type="int")
    enum VehiclePropertyAdas {
        ABS_IS_ENABLED = 0x1000, // boolean, READ_WRITE, GLOBAL
        ABS_IS_ENGAGED = 0x1001, // boolean, READ, GLOBAL
    }

    Example output (BODY domain, base=0x2000):
    package android.hardware.automotive.vehicle;
    @VintfStability
    @Backing(type="int")
    enum VehiclePropertyBody {
        LIGHTS_BEAM_HIGH = 0x2000, // boolean, READ_WRITE, GLOBAL
        LIGHTS_HAZARD    = 0x2001, // boolean, READ_WRITE, GLOBAL
    }
    """
    domain:       str = dspy.InputField(
        desc="HAL domain name, e.g. ADAS, POWERTRAIN, BODY"
    )
    properties:   str = dspy.InputField(
        desc="VSS property specifications including name, type, and access mode"
    )
    aosp_context: str = dspy.InputField(
        desc="Retrieved real AOSP .aidl examples to use as structural reference"
    )
    aidl_code:    str = dspy.OutputField(
        desc="Complete .aidl file content, starting with 'package' declaration"
    )


class ModernCppVehicleHardwareSignature(dspy.Signature):
    """Generate production-ready C++ Vehicle HAL for Android 14+ AIDL V3 only.
    Must follow AOSP reference: implement IVehicleHardware + wrap with DefaultVehicleHal.
    getValues and setValues must be ASYNCHRONOUS (callback + request).
    Never use any HIDL patterns.
    """
    domain: str = dspy.InputField(desc="HAL domain name (e.g. CHASSIS, POWERTRAIN)")
    properties: str = dspy.InputField(desc="VSS property specifications as string (from to_llm_spec())")
    aosp_context: str = dspy.InputField(desc="Retrieved high-quality AOSP examples + contract rules")
    
    cpp_header: str = dspy.OutputField(desc="Full content of VssVehicleHardware.h")
    cpp_impl: str = dspy.OutputField(desc="Full content of VssVehicleHardware.cpp")
    main_service: str = dspy.OutputField(desc="Full content of VehicleService.cpp")
    android_bp: str = dspy.OutputField(desc="Full content of Android.bp")
    reasoning: str = dspy.OutputField(desc="Reasoning for AOSP compliance")

class CppVehicleAssertions(dspy.Module):
    def __init__(self, strict: bool = False):
        super().__init__()
        self.strict = strict

    def forward(self, pred):
        header = getattr(pred, "cpp_header", "") or ""
        impl = getattr(pred, "cpp_impl", "") or ""
        main = getattr(pred, "main_service", "") or ""
        full = header + impl + main

        violations = []

        if "IVehicleHardware" not in header:
            violations.append("Must inherit from IVehicleHardware")
        if "DefaultVehicleHal" not in main:
            violations.append("Must use DefaultVehicleHal wrapper in main_service")
        if "AServiceManager_addService" not in main:
            violations.append("Must register using AServiceManager_addService")
        if not ("GetValueRequest" in full and "GetValuesCallback" in full):
            violations.append("getValues must use async pattern (GetValueRequest + GetValuesCallback)")
        if not ("SetValueRequest" in full and "SetValuesCallback" in full):
            violations.append("setValues must use async pattern")

        forbidden = ["HIDL_FETCH", "hidl/", "Return<", ".valueType"]
        for term in forbidden:
            if term in full:
                violations.append(f"Forbidden HIDL pattern: {term}")

        pred.violations = violations
        return pred


class SELinuxSignature(dspy.Signature):
    """
    Generate a complete SELinux Type Enforcement (.te) policy file for an
    AOSP VHAL service. The policy must allow the HAL service to operate
    correctly within Android's mandatory access control framework.

    Requirements:
    - Define the HAL service type using hal_attribute_hwservice macro
    - Add binder_call permissions between hal_client_domain and the service
    - Include add_hwservice and find_hwservice rules
    - Add necessary file access rules for /dev/vndbinder
    - Follow retrieved AOSP SELinux examples for macro and rule patterns
    - Use hal_vehicle naming conventions for vehicle HAL policies
    """
    domain:       str = dspy.InputField(
        desc="HAL domain name"
    )
    service_name: str = dspy.InputField(
        desc="Full VHAL service name, e.g. vendor.vss.adas"
    )
    aosp_context: str = dspy.InputField(
        desc="Retrieved real AOSP .te policy file examples"
    )
    policy:       str = dspy.OutputField(
        desc="Complete SELinux .te policy file content"
    )


class BuildFileSignature(dspy.Signature):
    """
    Generate a complete Android.bp build file for a VHAL HAL module.
    The build file declares a cc_binary for the C++ implementation
    so the module can be compiled by Soong.

    Requirements:
    - cc_binary name MUST be vendor.vss.<domain>-service
      (NOT android.hardware.automotive.vehicle-service, that already exists in AOSP)
    - MUST include: vendor: true
    - MUST include: relative_install_path: "hw"
    - List shared_libs: libbinder_ndk, libbase, liblog, libutils
    - List static_libs: android.hardware.automotive.vehicle-V3-ndk
    - DO NOT use HIDL library names (android.hardware.automotive.vehicle@2.0)
    - Follow retrieved Android.bp examples for block structure
    """
    module_name:  str = dspy.InputField(
        desc="HAL module name, e.g. vendor.vss.adas"
    )
    dependencies: str = dspy.InputField(
        desc="Required AOSP shared libraries and AIDL deps"
    )
    aosp_context: str = dspy.InputField(
        desc="Retrieved real Android.bp examples from AOSP hardware/interfaces"
    )
    build_file:   str = dspy.OutputField(
        desc="Complete Android.bp file content"
    )


class VINTFSignature(dspy.Signature):
    """
    Generate a VINTF manifest fragment XML and a corresponding init.rc
    service definition for registering a VHAL service with Android's
    hardware service manager.

    Requirements:
    - manifest.xml: include <hal> block with correct format, name, transport,
      version, and <interface> entries
    - init.rc: define service block with correct user/group (system/system),
      capabilities, and class (hal)
    - Use HIDL or AIDL transport as appropriate for Android 14
    - Follow retrieved VINTF and init.rc examples for exact syntax
    """
    domain:       str = dspy.InputField(
        desc="HAL domain name"
    )
    hal_version:  str = dspy.InputField(
        desc="HAL interface version, e.g. 2.0 or 1"
    )
    aosp_context: str = dspy.InputField(
        desc="Retrieved real VINTF manifest.xml and init.rc examples"
    )
    manifest:     str = dspy.OutputField(
        desc="Complete VINTF manifest XML followed by init.rc content, "
             "separated by a '# --- init.rc ---' comment line"
    )


# ═══════════════════════════════════════════════════════════════════
# B. DESIGN LAYER  (2 signatures)
# ═══════════════════════════════════════════════════════════════════

class DesignDocSignature(dspy.Signature):
    """
    Generate a comprehensive technical design document in Markdown for
    an AOSP VHAL module. The document should be suitable for inclusion
    in a Master's thesis appendix and for developer onboarding.

    Requirements:
    - Include sections: Overview, Architecture, HAL Properties, Data Flow,
      Security (SELinux), Build System, Testing, and API Reference
    - Use clear Markdown headings (##, ###)
    - Include property tables with name, type, access, and description
    - Reference the AOSP source conventions shown in retrieved examples
    - Minimum 500 words, accurate technical content
    """
    domain:       str = dspy.InputField(
        desc="HAL domain name"
    )
    modules:      str = dspy.InputField(
        desc="Module names with signal counts, one per line"
    )
    aosp_context: str = dspy.InputField(
        desc="Retrieved AOSP design doc and README examples for reference"
    )
    design_doc:   str = dspy.OutputField(
        desc="Complete Markdown design document starting with '# <Domain> HAL Design'"
    )


class PlantUMLSignature(dspy.Signature):
    """
    Generate a PlantUML architecture diagram source file for an AOSP VHAL
    system. The diagram should clearly show component relationships,
    data flow, and system boundaries.

    Requirements:
    - Start with @startuml and end with @enduml
    - Show: VSS layer → HAL layer → Car Service → Android App
    - Use package blocks for logical groupings
    - Show arrows with labels for data direction and protocol
    - Include a legend or title
    - Keep the diagram readable — max 20 components
    """
    domain:       str = dspy.InputField(
        desc="HAL domain name"
    )
    components:   str = dspy.InputField(
        desc="System component names and their relationships"
    )
    aosp_context: str = dspy.InputField(
        desc="Retrieved PlantUML diagram examples for reference"
    )
    puml:         str = dspy.OutputField(
        desc="Complete PlantUML source starting with @startuml"
    )


# ═══════════════════════════════════════════════════════════════════
# C. ANDROID APP LAYER  (2 signatures)
# ═══════════════════════════════════════════════════════════════════

class AndroidAppSignature(dspy.Signature):
    """
    Generate a complete Android Automotive OS app Fragment in Kotlin that
    reads and displays HAL property values using the CarPropertyManager API.

    Requirements:
    - Extend Fragment() with correct lifecycle methods
    - Obtain CarPropertyManager via Car.createCar() and car.getCarManager()
    - Register CarPropertyEventCallback for each property
    - Handle READ_WRITE properties with appropriate UI controls (Switch, Slider)
    - Handle READ-only properties with TextView displays
    - Include proper permission checks for Car API access
    - Use ViewBinding or findViewById correctly
    - Follow retrieved CarPropertyManager Kotlin examples for API usage
    - Include error handling for CarNotConnectedException
    """
    domain:       str = dspy.InputField(
        desc="HAL domain name"
    )
    properties:   str = dspy.InputField(
        desc="HAL property IDs and types, one per line"
    )
    aosp_context: str = dspy.InputField(
        desc="Retrieved real CarPropertyManager Kotlin/Java source examples"
    )
    kotlin_code:  str = dspy.OutputField(
        desc="Complete Kotlin Fragment file with package, imports, and class definition"
    )


class AndroidLayoutSignature(dspy.Signature):
    """
    Generate an Android XML layout file for displaying HAL property values
    in an Android Automotive OS app. The layout should be clear and usable
    on a vehicle infotainment screen.

    Requirements:
    - Use ConstraintLayout or LinearLayout as root
    - Include a ScrollView for long property lists
    - Display each property with a label (TextView) and value view
    - Use Switch for boolean READ_WRITE properties
    - Use SeekBar/Slider for numeric READ_WRITE properties
    - Use TextView for READ-only properties
    - Set correct android:id values matching the Fragment's ViewBinding
    - Use appropriate text sizes for in-car display (min 14sp)
    """
    domain:       str = dspy.InputField(
        desc="HAL domain name"
    )
    properties:   str = dspy.InputField(
        desc="Property names and types to display in the layout"
    )
    aosp_context: str = dspy.InputField(
        desc="Retrieved Android layout XML examples"
    )
    layout_xml:   str = dspy.OutputField(
        desc="Complete Android layout XML starting with <?xml version='1.0'?>"
    )


# ═══════════════════════════════════════════════════════════════════
# D. BACKEND LAYER  (3 signatures)
# ═══════════════════════════════════════════════════════════════════

class BackendAPISignature(dspy.Signature):
    """
    Generate a complete FastAPI Python backend server that exposes VHAL
    HAL properties via a REST API. The server bridges between the AOSP
    HAL layer and external clients (dashboards, test tools, simulators).

    Requirements:
    - Use FastAPI with async/await throughout
    - Define GET endpoints for all properties (READ and READ_WRITE)
    - Define PUT/POST endpoints for READ_WRITE properties
    - Use Pydantic models for request/response validation
    - Include CORS middleware for browser access
    - Include /health and /properties/list utility endpoints
    - Include WebSocket endpoint for real-time property change streaming
    - Use proper HTTP status codes (200, 404, 422, 500)
    - Add OpenAPI tags grouping endpoints by domain
    """
    domain:       str = dspy.InputField(
        desc="HAL domain name"
    )
    properties:   str = dspy.InputField(
        desc="HAL properties with types and access modes, one per line"
    )
    aosp_context: str = dspy.InputField(
        desc="Retrieved FastAPI and VHAL bridge examples for reference"
    )
    api_code:     str = dspy.OutputField(
        desc="Complete FastAPI main.py with all imports, models, and endpoints"
    )


class BackendModelSignature(dspy.Signature):
    """
    Generate complete Pydantic data model definitions for VHAL HAL
    properties to be used in the FastAPI backend server.

    Requirements:
    - Define a base PropertyValue model with property_id, value, timestamp
    - Define domain-specific models for each property type group
    - Use correct Python types: bool for BOOLEAN, float for FLOAT,
      int for INT/INT32/INT64, str for STRING
    - Add Field validators where appropriate (e.g. value ranges)
    - Include model Config with schema_extra examples
    - Export an ALL_MODELS dict mapping property_id → model class
    """
    properties:   str = dspy.InputField(
        desc="HAL property names and types, one per line"
    )
    aosp_context: str = dspy.InputField(
        desc="Retrieved Pydantic model and VHAL data type examples"
    )
    models_code:  str = dspy.OutputField(
        desc="Complete models.py file with all Pydantic class definitions"
    )


class SimulatorSignature(dspy.Signature):
    """
    Generate a Python HAL property simulator that produces realistic
    time-varying values for all properties in the domain. Used for
    testing the backend API and Android app without real vehicle hardware.

    Requirements:
    - Define a Simulator class with start(), stop(), and get_value() methods
    - Use asyncio for non-blocking value generation
    - Produce realistic value ranges per property type:
        BOOLEAN:  toggle with configurable probability
        FLOAT:    smooth random walk within realistic bounds
        INT:      step changes within discrete value sets
    - Expose a /ws WebSocket stream of property updates
    - Include a main() entry point that runs the simulator standalone
    - Log simulated values at configurable interval (default 1s)
    """
    domain:          str = dspy.InputField(
        desc="HAL domain name"
    )
    properties:      str = dspy.InputField(
        desc="Property names, types, and realistic value ranges"
    )
    aosp_context:    str = dspy.InputField(
        desc="Retrieved VHAL simulator and property value examples"
    )
    simulator_code:  str = dspy.OutputField(
        desc="Complete simulator.py with Simulator class and main() entry point"
    )


# ═══════════════════════════════════════════════════════════════════
# Registry — used by optimizer.py and hal_modules.py
# ═══════════════════════════════════════════════════════════════════
SIGNATURE_REGISTRY: dict[str, tuple] = {
    "aidl":           (AIDLSignature,         "aidl_code"),
    "cpp":            (ModernCppVehicleHardwareSignature, "cpp_impl"),
    "selinux":        (SELinuxSignature,       "policy"),
    "build":          (BuildFileSignature,     "build_file"),
    "vintf":          (VINTFSignature,         "manifest"),
    "design_doc":     (DesignDocSignature,     "design_doc"),
    "puml":           (PlantUMLSignature,      "puml"),
    "android_app":    (AndroidAppSignature,    "kotlin_code"),
    "android_layout": (AndroidLayoutSignature, "layout_xml"),
    "backend":        (BackendAPISignature,    "api_code"),
    "backend_model":  (BackendModelSignature,  "models_code"),
    "simulator":      (SimulatorSignature,     "simulator_code"),
}

print("✅ hal_signatures.py loaded with ModernCppVehicleHardwareSignature")