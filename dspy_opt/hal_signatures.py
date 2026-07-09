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

import re

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
    - CRITICAL FILE ORDER (violations cause build failure):
        LINE 1: package android.hardware.automotive.vehicle;
        LINE 2: @VintfStability
        LINE 3: @Backing(type="int")
        LINE 4: enum VehiclePropertyAdas {
      The package declaration MUST be the FIRST line — before ANY annotation.
      Never put @VintfStability or @Backing before the package declaration.
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
    """Generate **production-ready pure AIDL V3 only** C++ Vehicle HAL for Android 14+.

    STRICT RULES — NO EXCEPTIONS, NO HIDL ANYWHERE:

    HEADER FILE (VehicleHalService{Domain}.h) MUST BE EXACTLY:
       #pragma once
       #include <IVehicleHardware.h>
       #include <VehicleHalTypes.h>
       #include <aidl/android/hardware/automotive/vehicle/VehicleProperty.h>
       #include <vector>
       #include <memory>
       #include <mutex>
       #include <string>

       namespace android::hardware::automotive::vehicle {
       using namespace aidl::android::hardware::automotive::vehicle;

       class VehicleHalService{Domain} : public IVehicleHardware {
       public:
           std::vector<VehiclePropConfig> getAllPropertyConfigs() const override;
           StatusCode getValues(std::shared_ptr<const GetValuesCallback> callback,
                                const std::vector<GetValueRequest>& requests) const override;
           StatusCode setValues(std::shared_ptr<const SetValuesCallback> callback,
                                const std::vector<SetValueRequest>& requests) override;
           DumpResult dump(const std::vector<std::string>& options) override;
           StatusCode checkHealth() override;
           void registerOnPropertyChangeEvent(
               std::unique_ptr<const PropertyChangeCallback> callback) override;
           void registerOnPropertySetErrorEvent(
               std::unique_ptr<const PropertySetErrorCallback> callback) override;
       private:
           mutable std::mutex mLock;
           // Each property is backed by a simulated hardware register file
           // under this vendor-writable directory — real file I/O, not RAM.
           static constexpr const char* kHwRegisterDir = "/data/vendor/vss_hw/{domain_lower}/";
           std::string registerPath(int32_t propId) const;   // kHwRegisterDir + hex(propId) + ".reg"
           bool readRegister(int32_t propId, VehiclePropValue& out) const;
           bool writeRegister(int32_t propId, const VehiclePropValue& in) const;
       };

       } // namespace android::hardware::automotive::vehicle

    IMPLEMENTATION FILE (VehicleHalService{Domain}.cpp) — REAL hardware-register
    file I/O with an explicit switch(propId), NOT an in-memory map, NOT a stub:
       #include "VehicleHalService{Domain}.h"
       #include <fstream>
       #include <sys/stat.h>

       namespace android::hardware::automotive::vehicle {
       using namespace aidl::android::hardware::automotive::vehicle;

       std::vector<VehiclePropConfig> VehicleHalService{Domain}::getAllPropertyConfigs() const {
           return {
               {.prop = static_cast<int32_t>(VehicleProperty::VEHICLE_CHILDREN_...),
                .access = VehiclePropertyAccess::READ},
           };
       }

       std::string VehicleHalService{Domain}::registerPath(int32_t propId) const {
           char buf[64];
           snprintf(buf, sizeof(buf), "%s%08x.reg", kHwRegisterDir, propId);
           return std::string(buf);
       }

       // Real file I/O — simulates a memory-mapped hardware register per property.
       // A switch(propId) dispatches per-property so each case is explicit about
       // which property it is servicing, matching how a real VHAL backed by
       // actual hardware registers/CAN signals would be structured.
       bool VehicleHalService{Domain}::readRegister(int32_t propId, VehiclePropValue& out) const {
           switch (propId) {
               case static_cast<int32_t>(VehicleProperty::VEHICLE_CHILDREN_...): {
                   std::ifstream f(registerPath(propId));
                   if (!f.good()) { out.prop = propId; out.value.int32Values = {0}; return true; }
                   int32_t v = 0; f >> v;
                   out.prop = propId; out.value.int32Values = {v};
                   return true;
               }
               // ... one case per property this domain owns ...
               default:
                   return false;
           }
       }

       bool VehicleHalService{Domain}::writeRegister(int32_t propId, const VehiclePropValue& in) const {
           mkdir(kHwRegisterDir, 0770);  // best-effort; already created by init.rc in production
           std::ofstream f(registerPath(propId), std::ios::trunc);
           if (!f.good()) return false;
           if (!in.value.int32Values.empty()) f << in.value.int32Values[0];
           return true;
       }

       StatusCode VehicleHalService{Domain}::getValues(
               std::shared_ptr<const GetValuesCallback> callback,
               const std::vector<GetValueRequest>& requests) const {
           std::lock_guard<std::mutex> lock(mLock);
           std::vector<GetValueResult> results;
           for (const auto& req : requests) {
               GetValueResult r;
               r.requestId = req.requestId;
               VehiclePropValue v;
               if (readRegister(req.prop.prop, v)) { r.status = StatusCode::OK; r.prop = v; }
               else { r.status = StatusCode::INVALID_ARG; }
               results.push_back(std::move(r));
           }
           (*callback)(results);
           return StatusCode::OK;
       }

       StatusCode VehicleHalService{Domain}::setValues(
               std::shared_ptr<const SetValuesCallback> callback,
               const std::vector<SetValueRequest>& requests) {
           std::lock_guard<std::mutex> lock(mLock);
           std::vector<SetValueResult> results;
           for (const auto& req : requests) {
               SetValueResult r;
               r.requestId = req.requestId;
               r.status = writeRegister(req.value.prop, req.value) ? StatusCode::OK : StatusCode::INVALID_ARG;
               results.push_back(std::move(r));
           }
           (*callback)(results);
           return StatusCode::OK;
       }

       DumpResult VehicleHalService{Domain}::dump(const std::vector<std::string>&) { return {}; }
       StatusCode VehicleHalService{Domain}::checkHealth() { return StatusCode::OK; }
       void VehicleHalService{Domain}::registerOnPropertyChangeEvent(
               std::unique_ptr<const PropertyChangeCallback>) {}
       void VehicleHalService{Domain}::registerOnPropertySetErrorEvent(
               std::unique_ptr<const PropertySetErrorCallback>) {}

       } // namespace android::hardware::automotive::vehicle

    getAllPropertyConfigs() MUST use enum constant names from AIDL enum in properties field:
       {.prop = static_cast<int32_t>(VehicleProperty::VEHICLE_CHILDREN_ADAS_...),
        .access = VehiclePropertyAccess::READ_WRITE}

    WHY THIS MATTERS: this domain's implementation performs REAL file I/O
    against /data/vendor/vss_hw/{domain}/ — a stand-in for a genuine
    hardware register interface. This is what makes the domain's SELinux
    policy meaningful: it must be granted access to that specific vendor
    data path (see the SELinux agent's contract), not just generic binder.

    NEVER output markdown fences, no extra explanation.

    FORBIDDEN:
    - VssVehicleHardware — wrong class name
    - Placeholder prop IDs like 0x12345678
    - Missing #pragma once in header
    - Missing namespace wrapper in both .h and .cpp
    - Missing #include "VehicleHalService{Domain}.h" in .cpp
    - getValues/setValues that call the callback with an empty vector and
      discard the request — this is a stub, not an implementation
    - An in-memory-only std::unordered_map with no file I/O — the register
      file backing IS the point; do not substitute it with a plain map
    - IOnPropertyChangeCallback, IOnPropertySetErrorCallback — Android 13 only
    - hidl_interface, @2.0, HIDL_FETCH_*, Return<>, .valueType
    - aidlvhal:: namespace prefix
    """
    domain: str = dspy.InputField(desc="HAL domain name (e.g. HVAC, ADAS, BODY)")
    properties: str = dspy.InputField(desc="List of VSS properties with name, type, access, and AIDL enum")
    aosp_context: str = dspy.InputField(desc="Retrieved AOSP AIDL V3 examples — prefer FakeVehicleHardware / DefaultVehicleHal patterns")

    cpp_header: str = dspy.OutputField(desc="Full VehicleHalService{Domain}.h — MUST have #pragma once, namespace wrapper, registerPath/readRegister/writeRegister members")
    cpp_impl: str = dspy.OutputField(desc="Full VehicleHalService{Domain}.cpp — MUST include own .h, namespace wrapper, switch(propId)-based readRegister/writeRegister backed by real file I/O under /data/vendor/vss_hw/{domain}/")
    reasoning: str = dspy.OutputField(desc="Brief reasoning about class name, prop IDs used, and which properties the switch-case handles")






class CppSkeletonSignature(dspy.Signature):
    """Generate the FULL VehicleHalService{Domain}.h header AND the
    VehicleHalService{Domain}.cpp implementation for EVERY method EXCEPT
    the body of getAllPropertyConfigs() — used as pass 1 of the chunked
    generation path for domains with more properties than fit in one
    LLM call (see CHUNK_SIZE in agents/rag_dspy_cpp_agent.py). The
    property entries themselves are generated separately, in chunks,
    and spliced into the placeholder marker below by Python — this
    keeps this call's output size independent of property count.

    HEADER FILE (VehicleHalService{Domain}.h) MUST BE EXACTLY:
       #pragma once
       #include <IVehicleHardware.h>
       #include <VehicleHalTypes.h>
       #include <aidl/android/hardware/automotive/vehicle/VehicleProperty.h>
       #include <vector>
       #include <memory>
       #include <mutex>
       #include <string>

       namespace android::hardware::automotive::vehicle {
       using namespace aidl::android::hardware::automotive::vehicle;

       class VehicleHalService{Domain} : public IVehicleHardware {
       public:
           std::vector<VehiclePropConfig> getAllPropertyConfigs() const override;
           StatusCode getValues(std::shared_ptr<const GetValuesCallback> callback,
                                const std::vector<GetValueRequest>& requests) const override;
           StatusCode setValues(std::shared_ptr<const SetValuesCallback> callback,
                                const std::vector<SetValueRequest>& requests) override;
           DumpResult dump(const std::vector<std::string>& options) override;
           StatusCode checkHealth() override;
           void registerOnPropertyChangeEvent(
               std::unique_ptr<const PropertyChangeCallback> callback) override;
           void registerOnPropertySetErrorEvent(
               std::unique_ptr<const PropertySetErrorCallback> callback) override;
       private:
           mutable std::mutex mLock;
           static constexpr const char* kHwRegisterDir = "/data/vendor/vss_hw/{domain_lower}/";
           std::string registerPath(int32_t propId) const;
           bool readRegister(int32_t propId, VehiclePropValue& out) const;
           bool writeRegister(int32_t propId, const VehiclePropValue& in) const;
       };

       } // namespace android::hardware::automotive::vehicle

    IMPLEMENTATION FILE (VehicleHalService{Domain}.cpp) MUST BE EXACTLY
    THIS STRUCTURE — getValues/setValues call readRegister/writeRegister,
    NEVER a stub that discards requests and returns an empty vector:
       #include "VehicleHalService{Domain}.h"
       #include <fstream>
       #include <sys/stat.h>

       namespace android::hardware::automotive::vehicle {
       using namespace aidl::android::hardware::automotive::vehicle;

       std::vector<VehiclePropConfig> VehicleHalService{Domain}::getAllPropertyConfigs() const {
           return {
               /*__PROPERTY_ENTRIES_PLACEHOLDER__*/
           };
       }

       std::string VehicleHalService{Domain}::registerPath(int32_t propId) const {
           char buf[64];
           snprintf(buf, sizeof(buf), "%s%08x.reg", kHwRegisterDir, propId);
           return std::string(buf);
       }

       // readRegister/writeRegister are generated per property in the
       // separate chunked calls (this skeleton pass does NOT write their
       // bodies) — but getValues/setValues below MUST call them, not stub.

       StatusCode VehicleHalService{Domain}::getValues(
               std::shared_ptr<const GetValuesCallback> callback,
               const std::vector<GetValueRequest>& requests) const {
           std::lock_guard<std::mutex> lock(mLock);
           std::vector<GetValueResult> results;
           for (const auto& req : requests) {
               GetValueResult r;
               r.requestId = req.requestId;
               VehiclePropValue v;
               if (readRegister(req.prop.prop, v)) { r.status = StatusCode::OK; r.prop = v; }
               else { r.status = StatusCode::INVALID_ARG; }
               results.push_back(std::move(r));
           }
           (*callback)(results);
           return StatusCode::OK;
       }

       StatusCode VehicleHalService{Domain}::setValues(
               std::shared_ptr<const SetValuesCallback> callback,
               const std::vector<SetValueRequest>& requests) {
           std::lock_guard<std::mutex> lock(mLock);
           std::vector<SetValueResult> results;
           for (const auto& req : requests) {
               SetValueResult r;
               r.requestId = req.requestId;
               r.status = writeRegister(req.value.prop, req.value) ? StatusCode::OK : StatusCode::INVALID_ARG;
               results.push_back(std::move(r));
           }
           (*callback)(results);
           return StatusCode::OK;
       }

       DumpResult VehicleHalService{Domain}::dump(const std::vector<std::string>&) { return {}; }
       StatusCode VehicleHalService{Domain}::checkHealth() { return StatusCode::OK; }
       void VehicleHalService{Domain}::registerOnPropertyChangeEvent(
               std::unique_ptr<const PropertyChangeCallback>) {}
       void VehicleHalService{Domain}::registerOnPropertySetErrorEvent(
               std::unique_ptr<const PropertySetErrorCallback>) {}

       } // namespace android::hardware::automotive::vehicle

    CRITICAL: the literal text `/*__PROPERTY_ENTRIES_PLACEHOLDER__*/`
    MUST appear EXACTLY ONCE, inside the `return { ... };` block of
    getAllPropertyConfigs(), and NOWHERE else. Do not add any actual
    property entries yourself — they are added separately.

    NEVER output markdown fences, no extra explanation.

    FORBIDDEN:
    - VssVehicleHardware — wrong class name
    - Missing #pragma once in header
    - Missing namespace wrapper in both .h and .cpp
    - Missing #include "VehicleHalService{Domain}.h" in .cpp
    - Omitting or duplicating the /*__PROPERTY_ENTRIES_PLACEHOLDER__*/ marker
    - getValues/setValues that call the callback with an empty vector and
      discard the request (`(*callback)({}); return StatusCode::OK;`) —
      this is a stub, not an implementation, and will be rejected
    - `boolValues` or `booleanValues` fields — RawPropValues has no such
      field; booleans use int32Values with 0/1
    - IOnPropertyChangeCallback, IOnPropertySetErrorCallback — Android 13 only
    - hidl_interface, @2.0, HIDL_FETCH_*, Return<>, .valueType
    """
    domain: str = dspy.InputField(desc="HAL domain name (e.g. HVAC, ADAS, BODY)")
    property_count: str = dspy.InputField(desc="Total number of properties this domain has (for context only — entries are generated separately)")
    aosp_context: str = dspy.InputField(desc="Retrieved AOSP AIDL V3 examples")

    cpp_header: str = dspy.OutputField(desc="Full VehicleHalService{Domain}.h — MUST have #pragma once, namespace wrapper, include VehicleProperty.h")
    cpp_impl: str = dspy.OutputField(desc="Full VehicleHalService{Domain}.cpp with the /*__PROPERTY_ENTRIES_PLACEHOLDER__*/ marker inside getAllPropertyConfigs()")
    reasoning: str = dspy.OutputField(desc="Brief reasoning about class name used")


class CppPropertyEntriesSignature(dspy.Signature):
    """Generate ONLY the VehiclePropConfig initializer-list entries for a
    CHUNK of VSS properties — used for domains with more properties than
    fit in one LLM call (see CHUNK_SIZE in agents/rag_dspy_cpp_agent.py).

    Output EXACTLY one initializer entry per property, in order, and
    NOTHING else — no method signature, no class, no namespace, no
    return statement, no markdown fences, no explanation.

    Each entry MUST look exactly like this (one per property, comma-terminated):
       {.prop = static_cast<int32_t>(VehicleProperty::PROPERTY_NAME),
        .access = VehiclePropertyAccess::READ},

    Use VehiclePropertyAccess::READ, ::WRITE, or ::READ_WRITE based on the
    property's access mode given in `properties`. Use enum constant names
    EXACTLY as they appear in the AIDL enum block in `properties` — do not
    invent or modify names.

    FORBIDDEN:
    - Markdown code fences (``` or ```cpp)
    - Any text before the first { or after the last },
    - Placeholder/fake property names
    - Raw hex prop IDs instead of the enum constant cast
    """
    domain: str = dspy.InputField(desc="HAL domain name (e.g. HVAC, ADAS, BODY)")
    properties: str = dspy.InputField(desc="THIS CHUNK ONLY: list of VSS properties with name, type, access, and AIDL enum")
    aosp_context: str = dspy.InputField(desc="Retrieved AOSP AIDL V3 examples")

    entries: str = dspy.OutputField(desc="One or more VehiclePropConfig initializer-list entries, nothing else")

class CppVehicleAssertions(dspy.Module):
    """Validates + auto-repairs the structural contract for domain HAL C++ output.

    Beyond flagging violations (for the DSPy repair loop), this module
    deterministically FIXES the four most common structural defects so a
    single bad LLM completion doesn't propagate into the build:
      1. Missing '#pragma once' in cpp_header
      2. cpp_impl missing '#include "VehicleHalService{Domain}.h"'
      3. cpp_header / cpp_impl missing the
         'namespace android::hardware::automotive::vehicle { ... }' wrapper
      4. cpp_impl missing the unified AIDL enum header
         (#include <aidl/.../VehicleProperty.h>)
    """

    _NS_OPEN = "namespace android::hardware::automotive::vehicle {"
    _NS_USING = "using namespace aidl::android::hardware::automotive::vehicle;"
    _NS_CLOSE = "} // namespace android::hardware::automotive::vehicle"

    def __init__(self, strict: bool = False, auto_fix: bool = True):
        super().__init__()
        self.strict = strict
        self.auto_fix = auto_fix

    @staticmethod
    def _class_name(domain: str) -> str:
        return f"VehicleHalService{domain.strip().capitalize()}"

    @staticmethod
    def _header_file_name(domain: str) -> str:
        return f"VehicleHalService{domain.strip().capitalize()}.h"

    @staticmethod
    def _aidl_header_path(domain: str) -> str:
        # All custom VSS properties are merged into the single VehicleProperty.aidl
        # in aidl_property/ on the build system — per-domain headers do NOT exist.
        return "aidl/android/hardware/automotive/vehicle/VehicleProperty.h"

    # ── Cross-check against the REAL generated AIDL enum ────────────
    # This is the architecture contract: CPP must only reference
    # property names that actually exist in the .aidl file the AIDL
    # sub-agent already wrote for this domain (injected into the
    # `properties` InputField by
    # RAGDSPyArchitectAgent._get_aidl_content()). Without this check,
    # the LLM can "hallucinate" a plausible-looking enum constant name
    # that compiles in its own head but does not exist in the real
    # enum, producing "no member named X in VehicleProperty enum" at AOSP compile time — the single
    # biggest remaining source of C++ build failures once the
    # structural defects (pragma once, includes, namespace) are fixed.

    _AIDL_CONST_RE = re.compile(
        r"^\s*(\w+)\s*=\s*(0x[0-9a-fA-F]+|\d+)\s*,",
        re.MULTILINE,
    )

    def _extract_real_aidl_constants(self, properties_text: str) -> set:
        """Pull every enum constant NAME out of the real generated .aidl
        content embedded in the `properties` field (after the
        '=== Generated AIDL enum ===' marker, if present — falls back
        to scanning the whole string if the marker is absent, since
        the regex only matches NAME = 0xHEX, lines anyway)."""
        if not properties_text:
            return set()
        marker = "=== Generated AIDL enum"
        idx = properties_text.find(marker)
        aidl_section = properties_text[idx:] if idx != -1 else properties_text
        return {m.group(1) for m in self._AIDL_CONST_RE.finditer(aidl_section)}

    def _extract_used_constants(self, impl: str, domain: str) -> set:
        """Pull every VehicleProperty::NAME reference used in cpp_impl
        via static_cast<int32_t>(VehicleProperty::NAME) or any other
        VehicleProperty:: usage. After the aidl_property merge, all
        properties live in the unified VehicleProperty enum regardless
        of domain — per-domain VehiclePropertyAdas:: etc. no longer exist.
        """
        if not impl:
            return set()
        pattern = re.compile(r"VehicleProperty::(\w+)")
        return {m.group(1) for m in pattern.finditer(impl)}

    def _strip_hallucinated_property_blocks(self, impl: str, domain: str,
                                             hallucinated: set) -> str:
        """Remove getAllPropertyConfigs() entries that reference a
        hallucinated (non-existent) enum constant, rather than letting
        a single bad name fail the whole file's compilation. Matches
        the common '{.prop = static_cast<int32_t>(VehicleProperty::NAME), ...}'
        block pattern and deletes just that one initializer entry.
        After the aidl_property merge, all properties use VehicleProperty::
        (not per-domain VehiclePropertyAdas:: etc.).
        """
        if not impl or not hallucinated:
            return impl
        for name in hallucinated:
            block_pattern = re.compile(
                r"\{\s*\.prop\s*=\s*static_cast<int32_t>\(VehicleProperty::" +
                re.escape(name) +
                r"\)[^{}]*\}\s*,?",
                re.DOTALL,
            )
            impl = block_pattern.sub("", impl)
        return impl

    def _ensure_pragma_once(self, header: str) -> str:
        if "#pragma once" in header:
            return header
        return "#pragma once\n" + header

    def _ensure_self_include(self, impl: str, domain: str) -> str:
        own_header = self._header_file_name(domain)
        if f'#include "{own_header}"' in impl:
            return impl
        return f'#include "{own_header}"\n' + impl

    def _ensure_aidl_include(self, code: str, domain: str) -> str:
        aidl_path = self._aidl_header_path(domain)
        if f"#include <{aidl_path}>" in code:
            return code
        own_header = self._header_file_name(domain)
        marker = f'#include "{own_header}"'
        new_include = f"#include <{aidl_path}>\n"

        if marker in code:
            # impl files: anchor right after the self-include, which is
            # itself always the very first line of a contract-correct
            # .cpp — keeps AIDL include alongside the other includes.
            return code.replace(marker, marker + "\n" + new_include, 1)

        # header files (no self-include marker): insert after the LAST
        # contiguous line in the existing #include/#pragma block, never
        # at the absolute top — inserting at line 0 would land this
        # include before #pragma once and before whatever line defines
        # int32_t (e.g. <cstdint> pulled in transitively via
        # IVehicleHardware.h), breaking the AIDL stub's own `: int32_t`
        # enum backing type.
        lines = code.splitlines()
        last_directive_idx = -1
        for i, line in enumerate(lines):
            stripped = line.strip()
            if stripped.startswith("#") or stripped == "":
                last_directive_idx = i
            else:
                break
        insert_at = last_directive_idx + 1
        new_lines = lines[:insert_at] + [new_include.rstrip("\n")] + lines[insert_at:]
        return "\n".join(new_lines) + ("\n" if code.endswith("\n") else "")

    def _ensure_namespace_wrapper(self, code: str) -> str:
        if self._NS_OPEN in code and self._NS_CLOSE in code:
            return code
        if self._NS_OPEN in code:
            # Namespace opened but not properly closed — leave as-is,
            # safer to flag than to mutate ambiguous braces.
            return code

        lines = code.splitlines()
        split_idx = 0
        for i, line in enumerate(lines):
            stripped = line.strip()
            if stripped.startswith("#") or stripped == "":
                split_idx = i + 1
            else:
                break
        includes = "\n".join(lines[:split_idx])
        body = "\n".join(lines[split_idx:])
        wrapped = (
            f"{includes}\n\n{self._NS_OPEN}\n{self._NS_USING}\n\n"
            f"{body}\n\n{self._NS_CLOSE}\n"
        )
        return wrapped

    def _ensure_aidl_using_directive(self, code: str) -> str:
        """Insert `using namespace aidl::...;` immediately after an
        EXISTING `namespace android::hardware::automotive::vehicle {`
        if it's not already present somewhere in the file.

        This is the companion fix to _ensure_namespace_wrapper(), which
        deliberately does nothing when _NS_OPEN is already present
        (correct behavior — it shouldn't try to re-wrap an
        already-correctly-bracketed file). But "namespace present" and
        "namespace contains the AIDL using-directive" are independent
        facts: real generated code has been seen with a properly opened
        and closed namespace block that simply never adds the
        using-directive inside it, leaving every `VehicleProperty{Domain}`
        reference unresolved at compile time (clang's own suggested fix
        is exactly this using-directive — see the "did you mean
        'aidl::android::hardware::automotive::vehicle::X'" diagnostic).
        """
        if self._NS_OPEN not in code or self._NS_USING in code:
            return code
        return code.replace(self._NS_OPEN, f"{self._NS_OPEN}\n{self._NS_USING}", 1)

    def forward(self, pred):
        header = getattr(pred, "cpp_header", "") or ""
        impl = getattr(pred, "cpp_impl", "") or ""
        main = getattr(pred, "main_service", "") or ""
        domain = getattr(pred, "domain", "") or ""
        properties = getattr(pred, "properties", "") or ""

        violations = []

        if "IVehicleHardware" not in header:
            violations.append("Must inherit from IVehicleHardware")
        if not ("GetValueRequest" in (header + impl) and "GetValuesCallback" in (header + impl)):
            violations.append("getValues must use async pattern (GetValueRequest + GetValuesCallback)")
        if not ("SetValueRequest" in (header + impl) and "SetValuesCallback" in (header + impl)):
            violations.append("setValues must use async pattern")
        if header and "#pragma once" not in header:
            violations.append("Missing #pragma once in header")
        if domain and impl and f'#include "{self._header_file_name(domain)}"' not in impl:
            violations.append(f'cpp_impl missing #include "{self._header_file_name(domain)}"')
        if header and self._NS_OPEN not in header:
            violations.append("Missing namespace wrapper in header")
        if impl and self._NS_OPEN not in impl:
            violations.append("Missing namespace wrapper in cpp_impl")
        # Namespace wrapper present but missing the AIDL using-directive
        # inside it: _ensure_namespace_wrapper() below only fires when
        # _NS_OPEN is absent entirely, so a file that already has
        # `namespace android::hardware::automotive::vehicle { ... }`
        # but never adds `using namespace aidl::...;` inside it slips
        # past both checks above. The symptom is a real compile error
        # ("use of undeclared identifier 'VehicleProperty{Domain}'; did
        # you mean 'aidl::.../VehicleProperty{Domain}'") since the
        # AIDL-namespaced enum is then only reachable via its fully
        # qualified name, not bare `VehicleProperty{Domain}::X` as the
        # generated code (correctly) writes it.
        if impl and self._NS_OPEN in impl and self._NS_USING not in impl:
            violations.append("Namespace wrapper present in cpp_impl but missing 'using namespace aidl::...' inside it")
        if header and self._NS_OPEN in header and self._NS_USING not in header:
            violations.append("Namespace wrapper present in header but missing 'using namespace aidl::...' inside it")
        if domain and impl and f"VehicleProperty{domain.strip().capitalize()}" in impl:
            aidl_path = self._aidl_header_path(domain)
            if f"#include <{aidl_path}>" not in impl:
                violations.append(
                    f"cpp_impl uses VehicleProperty{domain.strip().capitalize()} "
                    f"enum but missing #include <{aidl_path}>"
                )

        # ── AIDL architecture cross-check ────────────────────────────
        # Only meaningful when we actually have the real generated AIDL
        # enum to compare against (properties contains the
        # "=== Generated AIDL enum ===" block RAGDSPyArchitectAgent
        # injects). Without it, an empty real_constants set means
        # "nothing to check against" — never flag hallucination based
        # on absence of ground truth.
        hallucinated = set()
        real_constants = self._extract_real_aidl_constants(properties)
        if domain and impl and real_constants:
            used_constants = self._extract_used_constants(impl, domain)
            hallucinated = used_constants - real_constants
            if hallucinated:
                violations.append(
                    "cpp_impl references property names not present in the "
                    f"generated AIDL enum (hallucinated): {sorted(hallucinated)}"
                )

        forbidden = ["HIDL_FETCH", "hidl/", "Return<", ".valueType"]
        for term in forbidden:
            if term in (header + impl + main):
                violations.append(f"Forbidden HIDL pattern: {term}")

        if self.auto_fix and domain:
            if header:
                header = self._ensure_pragma_once(header)
                needs_aidl_include = (
                    f"VehicleProperty{domain.strip().capitalize()}" in header
                    or "aidl::android::hardware::automotive::vehicle" in header
                    or self._NS_OPEN not in header
                    # ^ wrapper below will inject 'using namespace aidl::...'
                    #   even if the header body never otherwise mentions AIDL
                )
                if needs_aidl_include:
                    header = self._ensure_aidl_include(header, domain)
                header = self._ensure_namespace_wrapper(header)
                header = self._ensure_aidl_using_directive(header)
            if impl:
                impl = self._ensure_self_include(impl, domain)
                # Trigger on both per-domain name (old path) AND unified
                # VehicleProperty:: (new path after aidl_property merge).
                # Without this, .cpp files using VehicleProperty::NAME
                # correctly get the enum from the transitively-included
                # header but lack the explicit include — fragile and
                # violates the self-contained include contract.
                if (f"VehicleProperty{domain.strip().capitalize()}" in impl
                        or "VehicleProperty::" in impl):
                    impl = self._ensure_aidl_include(impl, domain)
                if hallucinated:
                    impl = self._strip_hallucinated_property_blocks(impl, domain, hallucinated)
                impl = self._ensure_namespace_wrapper(impl)
                impl = self._ensure_aidl_using_directive(impl)

            pred.cpp_header = header
            pred.cpp_impl = impl

        pred.violations = violations
        return pred


class SELinuxSignature(dspy.Signature):
    """Generate SELinux Type Enforcement (.te) policy fragments for a VSS
    property domain running INSIDE the shared VssVehicleHardware service.

    ARCHITECTURE NOTE — READ CAREFULLY:
    All VSS domains (ADAS, Body, Cabin, ...) are compiled into ONE binary
    and run as ONE process at runtime: the 'hal_vehicle_vss' domain,
    already declared and initialised elsewhere (init_daemon_domain,
    hal_server_domain, binder_use, exec_type — do NOT redeclare these).
    There is no separate daemon per VSS domain — matching real AOSP
    devices, which likewise expose exactly one Vehicle HAL process.

    REAL HARDWARE ACCESS THIS DOMAIN NEEDS:
    Every domain's C++ implementation reads/writes a simulated hardware
    register file per property under /data/vendor/vss_hw/{domain}/ (see
    the CPP agent's contract — this is real file I/O via std::ifstream/
    std::ofstream, not an in-memory-only map). This means EVERY domain
    genuinely needs vendor data file access — this is not optional or
    domain-specific like a camera or LED would be; it is required by the
    HAL implementation itself.

    STRICT RULES — NO EXCEPTIONS:
    - Output ONLY the raw .te policy content.
    - NEVER use markdown fences (no ```te, no ```, no code blocks)
    - Do NOT add any extra text, explanations, or leading '{'
    - This is ANDROID 14 AIDL — NOT HIDL. Never use any HIDL macros.

    FORBIDDEN — do NOT declare a new domain for this VSS property group:
    - type <anything>, domain;
    - type <anything>_exec, exec_type, vendor_file_type, file_type;
    - init_daemon_domain(...), hal_server_domain(...), binder_use(...)
    - binder_call(...) — binder access is already granted to hal_vehicle_vss

    FORBIDDEN (HIDL — causes build failure on Android 14):
    - hal_attribute_hwservice, add_hwservice, find_hwservice
    - hwservice_manager, hwbinder_device, fwk_vehicle_hwservice

    REQUIRED — every rule targets the single shared runtime domain and
    the vendor data type that backs this domain's hardware register files:
       allow hal_vehicle_vss vss_hw_data_file:dir { search add_name write };
       allow hal_vehicle_vss vss_hw_data_file:file { create read write open getattr unlink };
    (vss_hw_data_file is declared once in the shared base policy — do not
    redeclare its type here, just emit the allow rules above.)
    If this domain ALSO needs something beyond the register-file access
    every domain shares (e.g. a genuinely domain-specific resource),
    add additional `allow hal_vehicle_vss <target>:<class> {...};` lines
    for that — but the two lines above are always required.
    """
    domain: str = dspy.InputField(desc="HAL domain name")
    service_name: str = dspy.InputField(desc="Full VHAL service name, e.g. vendor.vss.adas")
    aosp_context: str = dspy.InputField(desc="Retrieved real AOSP 14 AIDL .te policy file examples")

    policy: str = dspy.OutputField(desc="allow-only .te fragment scoped to hal_vehicle_vss, granting vss_hw_data_file access — no new domain/type declarations")


class BuildFileSignature(dspy.Signature):
    """
    Generate a complete Android.bp build file for a VHAL HAL module.
    The build file declares a cc_binary for the C++ implementation
    so the module can be compiled by Soong.

    CRITICAL REQUIREMENTS — MUST FOLLOW EXACTLY:
    - cc_binary name MUST be: vendor.vss.<domain>-service
    - MUST include: vendor: true
    - MUST include: relative_install_path: "hw"
    - shared_libs must contain: libbinder_ndk, libbase, liblog, libutils
    - static_libs must contain: android.hardware.automotive.vehicle-V3-ndk
    - Use proper Soong syntax with correct indentation and colons
    - DO NOT use any HIDL-related names (@2.0, hidl, etc.)
    - Follow retrieved Android.bp examples for block structure

    Example structure:
    cc_binary {
        name: "vendor.vss.adas-service",
        srcs: ["*.cpp"],
        vendor: true,
        relative_install_path: "hw",
        shared_libs: ["libbinder_ndk", "libbase", ...],
        static_libs: ["android.hardware.automotive.vehicle-V3-ndk"],
    }
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
    service definition for registering an AIDL VHAL service on Android 14.

    ANDROID 14 AIDL — NOT HIDL. Never use hwbinder or HIDL transport.

    FORBIDDEN:
    - <transport>hwbinder</transport>
    - <fqname>@2.0::IVehicle/default</fqname>
    - Any HIDL version format (e.g. 2.0, 1.0)
    - markdown fences (no ```xml or ```rc)
    - any extra explanation or commentary

    OUTPUT FORMAT — follow this structure EXACTLY (two sections, literal separator):

    <manifest version="1.0" type="device" xmlns:android="http://schemas.android.com/apk/res/android">
        <hal format="aidl">
            <name>android.hardware.automotive.vehicle</name>
            <version>2</version>
            <interface>
                <name>IVehicle</name>
                <instance>default</instance>
            </interface>
        </hal>
    </manifest>
    # --- init.rc ---
    service vendor.vehicle-hal-default /vendor/bin/hw/android.hardware.automotive.vehicle-V2-default-service
        class hal
        user system
        group system

    Rules:
    - The XML block comes FIRST, then the literal line "# --- init.rc ---", then the init.rc block.
    - <hal format="aidl"> — no format="hidl", no <transport> element.
    - <version> must be a plain integer (e.g. 2), never "2.0".
    - <name> inside <hal> must be android.hardware.automotive.vehicle.
    - init.rc service must have: class hal, user system, group system.
    - Follow retrieved AOSP 14 AIDL VINTF examples for exact syntax.
    """
    domain:       str = dspy.InputField(
        desc="HAL domain name"
    )
    hal_version:  str = dspy.InputField(
        desc="HAL interface version integer, e.g. 2 (AIDL, not 2.0 HIDL)"
    )
    aosp_context: str = dspy.InputField(
        desc="Retrieved real AOSP 14 AIDL VINTF manifest.xml and init.rc examples"
    )
    manifest:     str = dspy.OutputField(
        desc=(
            "VINTF manifest XML block, then the literal separator line "
            "'# --- init.rc ---', then the init.rc service block. "
            "No markdown fences. No extra text before or after."
        )
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
    CRITICAL ESCAPING RULE — ALWAYS FOLLOW:
    In EVERY android:text="..." attribute, escape these characters:
      &  →  &amp;
      <  →  &lt;
      >  →  &gt;
    Never output raw VSS property names that contain & or <.

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
    Generate complete, syntactically valid Pydantic v2 models for VHAL properties.

    CRITICAL RULES — MUST FOLLOW:
    - Use proper Pydantic v2 syntax: from pydantic import BaseModel, Field, ConfigDict
    - Every field must have a colon ":" after the name (e.g. value: int = Field(...))
    - Use model_config = ConfigDict(...) instead of class Config
    - No trailing commas that break syntax
    - End every class properly
    - Export ALL_MODELS = { "property_name": ModelClass, ... }

    Example of correct output:
    from datetime import datetime
    from typing import Union, Optional
    from pydantic import BaseModel, Field, ConfigDict

    class PropertyValue(BaseModel):
        property_id: str = Field(..., description="...")
        value: Union[bool, float, int, str] = Field(...)
        timestamp: datetime = Field(...)

        model_config = ConfigDict(use_enum_values=True)

    class SomeProperty(PropertyValue):
        value: bool = Field(...)

    ALL_MODELS = {
        "VEHICLE_CHILDREN_...": SomeProperty,
    }
    """
    properties:   str = dspy.InputField(desc="HAL property names and types")
    aosp_context: str = dspy.InputField(desc="Pydantic examples")
    models_code:  str = dspy.OutputField(desc="Complete valid models.py")


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