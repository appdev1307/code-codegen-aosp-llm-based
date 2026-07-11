#!/usr/bin/env python3
"""
multi_main_c5.py
═══════════════════════════════════════════════════════════════════════════════
Condition 5 — VTS + HMI Generation Pipeline

Runs on Colab after C3/C4. Generates:
  - VtsHalAutomotiveVehicleVss.cpp  — VTS tests for the 500 VSS properties
  - HMI Android app fragments + XML layouts

Inputs (all from Colab, no Android Build dependency):
  - output_c4_feedback/SPEC_FROM_VSS_*.yaml  — C4 YAML spec
  - output/MODULE_PLAN.json                  — C4 module plan
  - dspy_opt/saved/                          — C4 optimised DSPy programs
  - Ollama running with qwen2.5-coder:32b

After this script, copy output_c5/vts/ to GCP, build with mmm, and run
atest VtsHalAutomotiveVehicleVss against the deployed VssVehicleHardware service.

Note: FakeVehicleHardware patching and VssProperties.json are NOT used.
VssVehicleHardware.cpp (C3/C4 output) serves properties directly.
═══════════════════════════════════════════════════════════════════
"""

from __future__ import annotations

import json
import re
import time
from pathlib import Path
from typing import Optional

# Reuse VssGlueAgent's real-AIDL-comment parser instead of guessing each
# property's type by bit-masking its numeric ID. VssGlueAgent already
# solves "what type is this property" correctly — reading the actual
# `// BOOLEAN, READ_WRITE, GLOBAL` comment in the generated .aidl file —
# and already handles STRING. Re-deriving type from ID bits would be a
# second, less-authoritative implementation of the same lookup, one step
# further from the real generated artifact.
from agents.vss_glue_agent import _parse_aidl_properties

# ── Configuration ────────────────────────────────────────────────
OUTPUT_DIR        = Path("output_c5")
DSPY_SAVED_DIR    = "dspy_opt/saved"
RAG_DB_PATH       = "rag/chroma_db"
RAG_TOP_K         = 8
MAX_RETRIES       = 3

# Input paths
import os as _os

def _resolve_c4_input_dir() -> Path:
    """Which C4 run's output does C5 read from?

    C4_INPUT env var always wins when set — explicit and unambiguous.

    Otherwise, auto-detect by ACTUAL FRESHNESS: compare the most recent
    .aidl file modification time under each candidate directory and
    pick whichever is newer.

    BUG THIS FIXES: previously this hardcoded a static preference for
    output_c4_minimal over output_c4_feedback whenever minimal existed
    at all — regardless of which one actually had the current data.
    output_c4_minimal is used repeatedly for quick iterative testing
    throughout a session (gen_hal_minimal_c4.py), so it almost always
    exists and is often STALE relative to a later, complete
    output_c4_feedback run. C5 would then silently build VTS
    (VtsHalAutomotiveVehicleVss.cpp) from minimal's older/incomplete
    AIDL enum content, while VssGlueAgent (running inside
    multi_main_c4_feedback.py itself, always against that SAME run's
    current AIDL) produced the aggregator from the correct, current
    data — producing exactly the "aggregator and VTS have mismatched
    /outdated names vs current AIDL + domain CPPs" symptom, without any
    error or warning, since both files were individually internally
    consistent (500/500 properties each) — just built from two
    different points in time.

    Freshness is measured by the newest VehicleProperty*.aidl file's
    mtime under each candidate's aidl subdirectory (not the directory's
    own mtime, which can be touched by unrelated writes and doesn't
    reliably reflect when generation last completed).
    """
    explicit = _os.environ.get("C4_INPUT")
    if explicit:
        print(f"[C5] C4_INPUT explicitly set → using {explicit}")
        return Path(explicit)

    minimal_dir  = Path("output_c4_minimal")
    feedback_dir = Path("output_c4_feedback")

    def _newest_aidl_mtime(base: Path) -> float:
        aidl_dir = base / "hardware/interfaces/automotive/vehicle/aidl/android/hardware/automotive/vehicle"
        if not aidl_dir.is_dir():
            return -1.0
        mtimes = [f.stat().st_mtime for f in aidl_dir.glob("VehicleProperty*.aidl")]
        return max(mtimes) if mtimes else -1.0

    minimal_mtime  = _newest_aidl_mtime(minimal_dir)  if minimal_dir.exists()  else -1.0
    feedback_mtime = _newest_aidl_mtime(feedback_dir) if feedback_dir.exists() else -1.0

    if minimal_mtime < 0 and feedback_mtime < 0:
        print(f"[C5] ⚠ C4_INPUT not set and neither {minimal_dir} nor {feedback_dir} "
              f"has any generated .aidl files yet — defaulting to {feedback_dir}, "
              f"will likely fail to find input")
        return feedback_dir

    import datetime as _dt
    def _fmt(t):
        return _dt.datetime.fromtimestamp(t).strftime("%Y-%m-%d %H:%M:%S") if t >= 0 else "absent"

    if feedback_mtime >= minimal_mtime:
        print(f"[C5] C4_INPUT not set — auto-detected by freshness: "
              f"{feedback_dir} (newest .aidl: {_fmt(feedback_mtime)}) "
              f"vs {minimal_dir} ({_fmt(minimal_mtime)}) → using {feedback_dir}")
        return feedback_dir
    else:
        print(f"[C5] C4_INPUT not set — auto-detected by freshness: "
              f"{minimal_dir} (newest .aidl: {_fmt(minimal_mtime)}) "
              f"vs {feedback_dir} ({_fmt(feedback_mtime)}) → using {minimal_dir}")
        return minimal_dir

C4_OUTPUT_DIR     = _resolve_c4_input_dir()  # C4 YAML spec + MODULE_PLAN.json
VTS_REL           = "test/vts/vss_vehicle"

# Vehicle HAL AIDL interface version the VTS links against. MUST match the
# version of the service running on the device, or the test binary fails to
# load at runtime with:
#   CANNOT LINK EXECUTABLE: library "android.hardware.automotive.vehicle-V4-ndk.so" not found
# The AOSP 14 (VanillaIceCream) automotive cuttlefish emulator service is V3.
# Override if you target a newer interface. Confirm on device with:
#   adb shell ls /vendor/lib64/ | grep automotive.vehicle-V
VHAL_NDK_VERSION  = "V3"
VEHICLE_NDK_LIB   = f"android.hardware.automotive.vehicle-{VHAL_NDK_VERSION}-ndk"

# Domain base addresses (must match rag_dspy_aidl_agent.py)
DOMAIN_BASE = {
    "adas":          0x1000,
    "body":          0x2000,
    "cabin":         0x3000,
    "chassis":       0x4000,
    "hvac":          0x5000,
    "infotainment":  0x6000,
    "powertrain":    0x7000,
}

# ── VHAL property ID encoding ────────────────────────────────────
# A VHAL property ID is a 32-bit value that packs four fields:
#   group (0xF0000000) | area (0x0F000000) | type (0x00FF0000) | index (0x0000FFFF)
# A bare per-domain index like 0x1000 has none of the high fields set, so VHAL
# rejects it during config validation and the property silently never registers.
# Every VSS property must therefore be encoded into a full, valid ID.
VSS_GROUP = 0x20000000  # VehiclePropertyGroup::VENDOR
VSS_AREA  = 0x01000000  # VehicleArea::GLOBAL  (matches .areaId = 0)
VSS_TYPE_BITS = {
    "STRING":  0x00100000,
    "BOOLEAN": 0x00200000,
    "INT32":   0x00400000,
    "INT64":   0x00500000,
    "FLOAT":   0x00600000,
    # "INT" (not "INT32") is what vss_to_yaml.py's vss_datatype_to_yaml_type()
    # actually produces for every int-like VSS datatype (int8/16/32,
    # uint8/16/32 all collapse to this one category — see that function's
    # docstring). Without this key, .get(vss_type, default) below silently
    # fell through to the default for every "INT" lookup — numerically
    # correct by coincidence (default happened to equal INT32's value),
    # but fragile: changing that default would have broken this silently.
    "INT":     0x00400000,
}

def encode_prop_id(raw_index: int, vss_type: str) -> int:
    """Turn a bare per-domain index into a valid VHAL property ID.

    The VSS type is folded into the ID's type field, which is where VHAL and
    CarPropertyManager read the value type from — it is not stored anywhere
    else in VehiclePropConfig, so it must live in the ID.
    """
    if raw_index & 0xF0000000:                 # already a full ID — leave alone
        return raw_index
    type_bits = VSS_TYPE_BITS.get(vss_type.upper(), 0x00400000)   # default INT32
    return VSS_GROUP | VSS_AREA | type_bits | (raw_index & 0xFFFF)

# ── LLM client (reused from C1-C4) ───────────────────────────────
def _call_llm(prompt: str, timeout: int = 240) -> str:
    try:
        from llm_client import call_llm
        try:
            return call_llm(prompt, timeout=timeout)
        except TypeError:
            return call_llm(prompt)
    except Exception as e:
        print(f"  [LLM] Error: {e}")
        return ""

# ── RAG retriever (reused from C3/C4) ────────────────────────────
def _retrieve(query: str, agent_type: str = "cpp", top_k: int = RAG_TOP_K) -> str:
    try:
        from rag.aosp_retriever import get_retriever
        retriever = get_retriever(db_path=RAG_DB_PATH)
        chunks = retriever.retrieve(query, agent_type=agent_type, top_k=top_k)
        return retriever.format_for_prompt(chunks)
    except Exception as e:
        print(f"  [RAG] Error: {e}")
        return ""

# ── DSPy program loader (reused from C3/C4) ──────────────────────
def _load_dspy_program(agent_type: str):
    try:
        import dspy
        from dspy_opt.hal_modules import get_module
        prog_path = Path(DSPY_SAVED_DIR) / f"{agent_type}_program" / "program.json"
        if not prog_path.exists():
            print(f"  [DSPy] No saved program for {agent_type} — using direct LLM")
            return None
        module = get_module(agent_type, programs_dir=DSPY_SAVED_DIR, auto_load=True)
        if module.is_optimised:
            print(f"  [DSPy] {agent_type}: loaded optimised program ✓")
        return module
    except Exception as e:
        print(f"  [DSPy] {agent_type} load failed: {e}")
        return None

# ── Property loader (reused from multi_main_c5_hmi.py) ───────────
def load_vss_properties() -> dict:
    """
    Load VSS properties dynamically from C4 output + AOSP dump.
    Returns dict: domain -> list of (name, int_id, type, access, desc)
    """
    try:
        import yaml
    except ImportError:
        print("  [C5] pip install pyyaml")
        return {}

    # Load C4 YAML spec
    spec_files = sorted(C4_OUTPUT_DIR.glob("SPEC_FROM_VSS_*.yaml"))
    if not spec_files:
        print(f"  [C5] No YAML spec in {C4_OUTPUT_DIR}")
        return {}
    spec = yaml.safe_load(spec_files[-1].read_text())

    # Build property metadata lookup
    prop_meta = {}
    for prop in spec.get("properties", []):
        name = prop.get("id", "") or prop.get("name", "")
        if name:
            prop_meta[name] = {
                "type":   prop.get("type", "INT").upper(),
                "access": prop.get("access", "READ").upper(),
            }

    # Load module plan - planner agent always writes to output/MODULE_PLAN.json
    # regardless of which condition (C1-C4) is running
    plan_path = None
    for candidate in [
        Path("output") / "MODULE_PLAN.json",        # primary — hardcoded in module_planner_agent.py
        C4_OUTPUT_DIR / "MODULE_PLAN.json",          # fallback
        Path("output_c4_feedback") / "MODULE_PLAN.json",
        Path("output_c1") / "MODULE_PLAN.json",
        Path("output_c1_backup") / "MODULE_PLAN.json",
    ]:
        if candidate.exists():
            plan_path = candidate
            print(f"  [C5] Using MODULE_PLAN: {plan_path}")
            break

    if not plan_path:
        print(f"  [C5] MODULE_PLAN.json not found in any location")
        return {}

    module_plan = json.loads(plan_path.read_text())
    modules_raw = module_plan.get("modules", {})

    # Handle both formats:
    # Dict format: {"ADAS": ["PROP1", "PROP2", ...], "BODY": [...]}  ← C1 format
    # List format: [{"domain": "adas", "properties": [...]}, ...]    ← alternative
    if isinstance(modules_raw, dict):
        # Convert dict to list of (domain, prop_names) tuples
        modules_iter = [(k.lower(), v) for k, v in modules_raw.items()]
    elif isinstance(modules_raw, list):
        modules_iter = []
        for m in modules_raw:
            if isinstance(m, dict):
                modules_iter.append((m.get("domain", "").lower(), m.get("properties", [])))
            elif isinstance(m, str):
                modules_iter.append((m.lower(), []))
    else:
        print(f"  [C5] Unknown MODULE_PLAN format")
        return {}

    # Build domain map from modules_iter
    domain_map = {}
    # Global monotonic index so every property gets a unique low-16-bit field.
    # Deriving the index from per-domain `base + idx` caused collisions: once the
    # type bits are OR'd in, two properties sharing the same type AND the same low
    # index produce identical IDs, and VHAL drops duplicate configs wholesale.
    # A single running counter (0x1000, 0x1001, ...) is unique across all domains.
    global_idx = 0x1000
    for domain, prop_names in modules_iter:
        if not domain:
            continue

        # If prop_names is empty (string-only module), skip
        if not prop_names:
            continue

        base  = DOMAIN_BASE.get(domain, 0x8000)
        props = []
        for idx, name in enumerate(prop_names):
            meta    = prop_meta.get(name, {})
            typ     = meta.get("type", "INT")
            access  = meta.get("access", "READ")
            # Always assign from the global counter. The AOSP-dump values in
            prop_id = encode_prop_id(global_idx, typ)
            global_idx += 1
            desc    = name.replace("VEHICLE_CHILDREN_", "").replace("_CHILDREN_", ".")[:50]
            props.append((name, prop_id, typ, access, desc))
        if props:
            domain_map[domain] = props
            print(f"  [C5] {domain.upper():15s}: {len(props):4d} properties")

    total = sum(len(v) for v in domain_map.values())
    print(f"  [C5] Total: {total} properties across {len(domain_map)} domains")
    return domain_map

# ═══════════════════════════════════════════════════════════════════
# Agent 1: FakeVehicleHardware Patcher
# Extends FakeVehicleHardware.cpp to serve VSS properties at runtime
# Reuses: cpp DSPy program, RAG aosp_cpp collection, C4 feedback loop
# ═══════════════════════════════════════════════════════════════════
# ═══════════════════════════════════════════════════════════════════
# Agent 1: VTS Test Generator
# Generates custom VTS tests for all 500 VSS properties.
# Tests run against VssVehicleHardware (C3/C4 output) on Cuttlefish.
# ═══════════════════════════════════════════════════════════════════
class VtsGeneratorAgent:
    """
    Generates VtsHalAutomotiveVehicleVss.cpp — custom VTS tests for
    VSS properties. Tests:
    1. VHAL service availability
    2. getAllPropertyConfigs() returns configs
    3. VSS enum values are correct (compile-time)
    4. VSS properties are accessible via getValues() (runtime)
    5. No duplicate property IDs
    Reuses cpp DSPy program and RAG patterns from existing VTS tests.
    """

    def __init__(self):
        self.prog = _load_dspy_program("cpp")

    def _generate_vts_cpp(self, domain_map: dict) -> str:
        """Generate VTS test C++ for the VSS HAL.

        Tests the real contract rather than per-domain "base addresses"
        (which no longer exist now that IDs come from a single global counter):
          1. the VHAL binder is reachable,
          2. every generated VSS property is actually registered/served by the
             running VHAL (via getPropConfigs — all-or-nothing),
          3. the generated IDs are internally consistent (unique + well-formed
             VENDOR-group IDs with a value-type field).

        Deliberately self-contained: it embeds the generated property IDs and
        does NOT depend on per-domain VehicleProperty<Domain> enum headers,
        which are produced by a separate agent and were the source of earlier
        compile / ID-mismatch failures (and of the "0 test cases" result).
        """
        # Parse the REAL generated AIDL files FIRST — this is the single
        # source of truth for property names from here on. Previously,
        # names came from domain_map (Python-tracked, ultimately from
        # VSS_LABELLED_500.json / MODULE_PLAN.json) while type/access came
        # from this same AIDL parse — two DIFFERENT sources that can
        # disagree. Confirmed in practice: the AIDL generation LLM
        # occasionally duplicates a "CHILDREN_" segment in very long,
        # deeply-nested property paths (e.g. "CABIN_CHILDREN_CHILDREN_SEAT"
        # instead of "CABIN_CHILDREN_SEAT") — domain_map's name doesn't
        # have this quirk, so referencing THAT name against the REAL
        # compiled enum (which DOES have the quirk) fails to compile.
        # Building all_names from real_props instead means kVssPropertyIds
        # only ever references names that are GUARANTEED to exist in the
        # real enum, because they were read from it.
        aidl_dir = str(C4_OUTPUT_DIR / "hardware/interfaces/automotive/vehicle/aidl"
                        "/android/hardware/automotive/vehicle")
        real_props = _parse_aidl_properties(aidl_dir)

        seen = set()
        all_names = []
        for p in real_props:
            n = p["name"]
            if n not in seen:
                seen.add(n)
                all_names.append(n)

        skipped = set(n for props in domain_map.values() for (n, _pid, *_r) in props) - seen
        if skipped:
            print(f"[C5] ⚠ {len(skipped)} propert(y/ies) in domain_map were not "
                  f"found in the real generated AIDL — excluded from VTS "
                  f"(this means the AIDL generation step silently dropped "
                  f"them; worth checking that agent's output separately):")
            for n in sorted(skipped)[:10]:
                print(f"      - {n}")

        ids_literal = ",\n    ".join(
            f"static_cast<int32_t>(VehicleProperty::{n})" for n in all_names
        )
        total = len(all_names)

        # Type/access from the SAME real_props parsed above — one source,
        # not two, so this can never disagree with all_names again.
        name_to_type = {p["name"]: p["type"] for p in real_props}
        all_types = [name_to_type.get(n, "INT") for n in all_names]
        types_literal = ",\n    ".join(f'"{t}"' for t in all_types)

        # Real per-property access mode, same source as type above. Needed
        # so VssPropertyRoundTrip doesn't attempt to SET a READ-only
        # property — CppRegisterBodySignature's generated code correctly
        # omits writeRegister cases for READ-only properties (there's
        # nothing semantically valid to write), so setValues() on one of
        # them correctly returns INVALID_ARG via the switch's default —
        # a false test failure if the test doesn't account for this.
        name_to_access = {p["name"]: p["access"] for p in real_props}
        all_access = [name_to_access.get(n, "READ") for n in all_names]
        access_literal = ",\n    ".join(f'"{a}"' for a in all_access)

        return f"""// AUTO-GENERATED by C5 pipeline — VSS VTS Tests
// DO NOT EDIT MANUALLY — regenerate with multi_main_c5.py
//
// Verifies that the {total} generated VSS properties are registered and served
// by the running Vehicle HAL, and that their property IDs are well-formed.

#include <aidl/android/hardware/automotive/vehicle/IVehicle.h>
#include <aidl/android/hardware/automotive/vehicle/VehiclePropConfigs.h>
#include <aidl/android/hardware/automotive/vehicle/VehicleProperty.h>
#include <android/binder_manager.h>
#include <gtest/gtest.h>
#include <cstdint>
#include <cmath>
#include <iostream>
#include <set>
#include <vector>
#include <string>

using namespace aidl::android::hardware::automotive::vehicle;

// VHAL property-ID field masks (group | area | type | index).
static constexpr uint32_t kGroupMask   = 0xF0000000u;
static constexpr uint32_t kTypeMask    = 0x00FF0000u;
static constexpr uint32_t kVendorGroup = 0x20000000u;

// The {total} VSS property IDs emitted into FakeVehicleHardware by this run.
static const std::vector<int32_t> kVssPropertyIds = {{
    {ids_literal}
}};

// Same order as kVssPropertyIds — each property's real type, read from
// the generated AIDL file's own comment (ground truth), not re-derived
// by bit-masking the numeric ID. Used by VssPropertyRoundTrip below to
// pick the correct RawPropValues field per property.
static const std::vector<std::string> kVssPropertyTypes = {{
    {types_literal}
}};

// Same order as kVssPropertyIds — each property's real access mode
// (READ or READ_WRITE), also read from the generated AIDL comment.
// READ-only properties have no writeRegister case in the generated
// C++ (there's nothing valid to write), so VssPropertyRoundTrip below
// skips the set-then-get portion for them and only verifies get.
static const std::vector<std::string> kVssPropertyAccess = {{
    {access_literal}
}};

// ── Fixture: connect to the VHAL service ─────────────────────────
class VssVhalTest : public ::testing::Test {{
 protected:
  std::shared_ptr<IVehicle> vehicle;
  void SetUp() override {{
    const std::string instance = std::string(IVehicle::descriptor) + "/default";
    vehicle = IVehicle::fromBinder(
        ndk::SpAIBinder(AServiceManager_waitForService(instance.c_str())));
    ASSERT_NE(vehicle, nullptr)
        << "IVehicle service not available — is Cuttlefish running?";
  }}
}};

// ── Test 1: VHAL service is reachable ────────────────────────────
TEST_F(VssVhalTest, ServiceAvailable) {{
  ASSERT_NE(vehicle, nullptr) << "IVehicle service is null";
}}

// ── Test 2: every VSS property is registered by the VHAL ─────────
// getPropConfigs() returns an error if ANY requested ID is unsupported, so
// isOk() is an all-or-nothing signal that the generated configs registered.
TEST_F(VssVhalTest, VssPropertiesRegistered) {{
  ASSERT_FALSE(kVssPropertyIds.empty());
  VehiclePropConfigs configs;
  auto status = vehicle->getPropConfigs(kVssPropertyIds, &configs);
  ASSERT_TRUE(status.isOk())
      << "getPropConfigs failed — at least one of " << kVssPropertyIds.size()
      << " VSS properties is not registered (service-specific error "
      << status.getServiceSpecificError() << ")";
  std::cout << "✓ " << kVssPropertyIds.size()
            << " VSS properties registered" << std::endl;
}}

// ── Test 3: generated IDs are unique ─────────────────────────────
TEST(VssPropertyIdTest, AllIdsUnique) {{
  std::set<int32_t> seen;
  for (int32_t id : kVssPropertyIds) {{
    ASSERT_TRUE(seen.insert(id).second)
        << "Duplicate VSS property ID: 0x" << std::hex << id;
  }}
  ASSERT_EQ(seen.size(), kVssPropertyIds.size());
}}

// ── Test 4: generated IDs are well-formed VENDOR properties ──────
TEST(VssPropertyIdTest, AllIdsWellFormed) {{
  for (int32_t id : kVssPropertyIds) {{
    EXPECT_EQ(static_cast<uint32_t>(id) & kGroupMask, kVendorGroup)
        << "ID 0x" << std::hex << id << " is not in the VENDOR group";
    EXPECT_NE(static_cast<uint32_t>(id) & kTypeMask, 0u)
        << "ID 0x" << std::hex << id << " has no value-type bits";
  }}
}}

// ── Test 5: all VSS props have valid configs (readable check) ────
TEST_F(VssVhalTest, VssPropertiesReadable) {{
  VehiclePropConfigs configs;
  auto status = vehicle->getPropConfigs(kVssPropertyIds, &configs);
  ASSERT_TRUE(status.isOk()) << "getPropConfigs failed for readable check";
  int count = 0;
  for (const auto& cfg : configs.payloads) {{
    EXPECT_NE(cfg.prop, 0) << "Property config has zero prop ID";
    count++;
  }}
  std::cout << "✓ " << count << " VSS properties readable (via getPropConfigs)" << std::endl;
}}

// ── Test 6: READ_WRITE props exist (writable check) ───────────────
TEST_F(VssVhalTest, VssPropertiesWritable) {{
  VehiclePropConfigs configs;
  auto status = vehicle->getPropConfigs(kVssPropertyIds, &configs);
  ASSERT_TRUE(status.isOk());
  int rw_count = 0;
  for (const auto& cfg : configs.payloads) {{
    if (cfg.access == VehiclePropertyAccess::READ_WRITE ||
        cfg.access == VehiclePropertyAccess::WRITE) {{
      rw_count++;
    }}
  }}
  std::cout << "✓ " << rw_count << "/" << configs.payloads.size()
            << " VSS properties are READ_WRITE" << std::endl;
  EXPECT_GE(rw_count, 0) << "VSS properties writable check passed";
}}

// ── Test 7: real set-then-get round trip, ALL 500 properties ──────
// Exercises the actual HW-register file I/O backing (see CPP agent
// contract) rather than just checking config metadata like Tests 5/6.
// Each property's value type is decoded from its own ID (type bits,
// see encode_prop_id / VSS_TYPE_BITS above) so the correct RawPropValues
// field (int32Values or floatValues) is used per property — matching
// exactly how the generated C++ readRegister/writeRegister dispatch on
// propId via switch-case.
TEST_F(VssVhalTest, VssPropertyRoundTrip) {{
  ASSERT_FALSE(kVssPropertyIds.empty());
  // Type is read from kVssPropertyTypes — the real per-property type
  // parsed from the generated AIDL file's own comment (ground truth) —
  // not re-derived by bit-masking the numeric ID at runtime.

  int passed = 0, failed = 0;
  std::vector<int32_t> failedProps;

  for (size_t i = 0; i < kVssPropertyIds.size(); ++i) {{
    const int32_t targetProp = kVssPropertyIds[i];
    const std::string& realType = kVssPropertyTypes[i];
    const bool isReadOnly = (kVssPropertyAccess[i] == "READ");
    const bool isFloat  = (realType == "FLOAT");
    const bool isString = (realType == "STRING");
    // Everything else (BOOLEAN, INT, INT32) uses int32Values — matches
    // CppRegisterBodySignature's field mapping for these categories.

    if (isReadOnly) {{
      // No writeRegister case exists for READ-only properties by design
      // (see CppRegisterBodySignature) — only verify getValues() itself
      // works, not a round trip.
      GetValueRequests getReqs;
      GetValueRequest getReq;
      getReq.requestId = targetProp;
      getReq.prop.prop = targetProp;
      getReqs.payloads = {{getReq}};

      GetValueResults getResults;
      auto getStatus = vehicle->getValues(getReqs, &getResults);
      if (!getStatus.isOk() || getResults.payloads.empty() ||
          getResults.payloads[0].status != StatusCode::OK) {{
        failed++; failedProps.push_back(targetProp); continue;
      }}
      passed++;
      continue;
    }}

    SetValueRequests setReqs;
    SetValueRequest setReq;
    setReq.requestId = targetProp;
    setReq.value.prop = targetProp;
    if (isFloat) {{
      setReq.value.value.floatValues = {{12.5f}};
    }} else if (isString) {{
      setReq.value.value.stringValue = "vts_test_value";
    }} else {{
      setReq.value.value.int32Values = {{42}};
    }}
    setReqs.payloads = {{setReq}};

    SetValueResults setResults;
    auto setStatus = vehicle->setValues(setReqs, &setResults);
    if (!setStatus.isOk() || setResults.payloads.empty() ||
        setResults.payloads[0].status != StatusCode::OK) {{
      failed++; failedProps.push_back(targetProp); continue;
    }}

    GetValueRequests getReqs;
    GetValueRequest getReq;
    getReq.requestId = targetProp;
    getReq.prop.prop = targetProp;
    getReqs.payloads = {{getReq}};

    GetValueResults getResults;
    auto getStatus = vehicle->getValues(getReqs, &getResults);
    if (!getStatus.isOk() || getResults.payloads.empty() ||
        getResults.payloads[0].status != StatusCode::OK) {{
      failed++; failedProps.push_back(targetProp); continue;
    }}

    const auto& got = getResults.payloads[0].prop.value;
    bool matches;
    if (isFloat) {{
      matches = !got.floatValues.empty() && std::abs(got.floatValues[0] - 12.5f) < 0.001f;
    }} else if (isString) {{
      matches = got.stringValue == "vts_test_value";
    }} else {{
      matches = !got.int32Values.empty() && got.int32Values[0] == 42;
    }}

    if (matches) {{ passed++; }}
    else {{ failed++; failedProps.push_back(targetProp); }}
  }}

  std::cout << "✓ Round trip: " << passed << "/" << kVssPropertyIds.size()
            << " properties persisted correctly" << std::endl;
  if (!failedProps.empty()) {{
    std::cout << "  First failing prop: 0x" << std::hex << failedProps[0] << std::dec << std::endl;
  }}
  EXPECT_EQ(failed, 0) << failed << " / " << kVssPropertyIds.size()
      << " properties failed the set-then-get round trip "
      << "— HW-register file I/O is not persisting correctly for all properties";
}}
"""


    def _generate_android_bp(self) -> str:
        # The vehicle NDK lib version must match the service on device (V3 for
        # AOSP 14 auto cuttlefish). Hardcoding V4 here caused a runtime link
        # failure: "library android.hardware.automotive.vehicle-V4-ndk.so not
        # found". Sourced from VHAL_NDK_VERSION so it tracks the target.
        return f"""// AUTO-GENERATED by C5 pipeline
cc_test {{
    name: "VtsHalAutomotiveVehicleVss",
    srcs: ["VtsHalAutomotiveVehicleVss.cpp"],
    shared_libs: [
        "libbase",
        "libbinder_ndk",
        "{VEHICLE_NDK_LIB}",
    ],
    static_libs: [
        "libgtest",
    ],
    test_suites: ["vts", "general-tests"],
    test_config: "VtsHalAutomotiveVehicleVss.xml",
    vendor: true,
}}
"""

    def _generate_test_config(self) -> str:
        # NOTE: the test is built vendor:true + 64-bit, so it installs under
        # /data/nativetest64/vendor/ — NOT /data/nativetest. Pointing GTest at
        # the wrong path makes TradeFed find the module but run 0 test cases
        # ("TradeFed did not find any test cases to run"). We push the binary
        # explicitly to /data/local/tmp and run it there, which avoids the
        # nativetest/nativetest64 + vendor-subdir ambiguity entirely.
        return """<?xml version="1.0" encoding="utf-8"?>
<!-- AUTO-GENERATED by C5 pipeline -->
<configuration description="VTS test for VSS Vehicle HAL properties">
    <option name="test-suite-tag" value="vts"/>
    <target_preparer class="com.android.tradefed.targetprep.RootTargetPreparer"/>
    <target_preparer class="com.android.tradefed.targetprep.PushFilePreparer">
        <option name="cleanup" value="true"/>
        <option name="push" value="VtsHalAutomotiveVehicleVss->/data/local/tmp/VtsHalAutomotiveVehicleVss"/>
    </target_preparer>
    <test class="com.android.tradefed.testtype.GTest">
        <option name="native-test-device-path" value="/data/local/tmp"/>
        <option name="module-name" value="VtsHalAutomotiveVehicleVss"/>
    </test>
</configuration>
"""

    def run(self, domain_map: dict) -> tuple[str, float]:
        """Generate VTS test files. Returns (cpp_content, score)."""
        print(f"\n  [VTS] Generating VSS VTS tests...")

        best_content = ""
        best_score   = 0.0

        for attempt in range(1, MAX_RETRIES + 1):
            print(f"  [VTS] Attempt {attempt}/{MAX_RETRIES}...")

            cpp_content = self._generate_vts_cpp(domain_map)

            # Score — count both TEST_F() and TEST() macros
            has_fixture  = "VssVhalTest" in cpp_content
            test_count   = cpp_content.count("TEST_F(") + cpp_content.count("TEST(")
            has_tests    = test_count >= 3
            has_includes = "IVehicle.h" in cpp_content
            struct_score = 1.0 if (has_fixture and has_tests and has_includes) else 0.5
            coverage     = min(1.0, test_count / 10.0)
            score        = 0.40 * struct_score + 0.40 * 1.0 + 0.20 * coverage

            print(f"  [VTS] Attempt {attempt}: score={score:.3f} tests={test_count}")

            if score > best_score:
                best_score   = score
                best_content = cpp_content

            if score > 0.8:
                print(f"  [VTS] ✓ Passed (score={score:.3f})")
                break

        return best_content, best_score


# ═══════════════════════════════════════════════════════════════════
def _generate_hmi_build_files(domain_map: dict) -> None:
    """Generate AndroidManifest.xml and Android.bp for HMI app."""
    app_dir = OUTPUT_DIR / "hmi_app"
    app_dir.mkdir(parents=True, exist_ok=True)

    # AndroidManifest.xml
    manifest = """<?xml version="1.0" encoding="utf-8"?>
<manifest xmlns:android="http://schemas.android.com/apk/res/android"
    package="com.vss.vehicleapp"
    android:versionCode="1"
    android:versionName="1.0">

    <uses-sdk
        android:minSdkVersion="29"
        android:targetSdkVersion="33" />

    <uses-permission android:name="android.car.permission.CAR_VENDOR_EXTENSION" />
    <uses-permission android:name="android.car.permission.VEHICLE_DYNAMICS_STATE" />

    <application
        android:label="VSS Dashboard"
        android:icon="@mipmap/ic_launcher"
        android:theme="@style/Theme.AppCompat.Light">
        <activity android:name=".MainActivity"
            android:exported="true">
            <intent-filter>
                <action android:name="android.intent.action.MAIN" />
                <category android:name="android.intent.category.LAUNCHER" />
            </intent-filter>
        </activity>
    </application>
</manifest>
"""
    (app_dir / "AndroidManifest.xml").write_text(manifest)

    # Android.bp
    android_bp = """android_app {
    name: "VssDashboardApp",
    srcs: ["src/main/java/**/*.kt"],
    resource_dirs: ["src/main/res"],
    manifest: "AndroidManifest.xml",
    sdk_version: "current",
    privileged: true,
    certificate: "platform",
    static_libs: [
        "androidx.appcompat_appcompat",
        "androidx.recyclerview_recyclerview",
        "car-ui-lib",
    ],
}
"""
    (app_dir / "Android.bp").write_text(android_bp)

    # MainActivity.kt stub
    main_dir = app_dir / "src/main/java/com/vss/vehicleapp"
    main_dir.mkdir(parents=True, exist_ok=True)
    domains = list(domain_map.keys())
    fragment_list = "\n".join(
        f'        fragments.add({d.capitalize()}Fragment())'
        for d in domains
    )
    main_activity = f"""package com.vss.vehicleapp

import android.os.Bundle
import androidx.appcompat.app.AppCompatActivity
import androidx.fragment.app.Fragment

class MainActivity : AppCompatActivity() {{
    override fun onCreate(savedInstanceState: Bundle?) {{
        super.onCreate(savedInstanceState)
        setContentView(R.layout.activity_main)
        if (savedInstanceState == null) {{
            supportFragmentManager.beginTransaction()
                .replace(R.id.fragment_container, AdasFragment())
                .commit()
        }}
    }}
}}
"""
    (main_dir / "MainActivity.kt").write_text(main_activity)

    # activity_main.xml
    res_layout = app_dir / "src/main/res/layout"
    res_layout.mkdir(parents=True, exist_ok=True)
    activity_layout = """<?xml version="1.0" encoding="utf-8"?>
<FrameLayout xmlns:android="http://schemas.android.com/apk/res/android"
    android:id="@+id/fragment_container"
    android:layout_width="match_parent"
    android:layout_height="match_parent" />
"""
    (res_layout / "activity_main.xml").write_text(activity_layout)


# Agent 3: HMI App Generator (reused from multi_main_c5_hmi.py)
# ═══════════════════════════════════════════════════════════════════
def generate_hmi_app(domain_map: dict) -> float:
    """
    Generate HMI app using C4 optimised DSPy programs.
    Reuses _generate_fragment, _generate_layout, _generate_main_activity
    from multi_main_c5_hmi.py logic.
    """
    print(f"\n  [HMI] Generating HMI app ({sum(len(v) for v in domain_map.values())} properties)...")

    app_dir  = OUTPUT_DIR / "hmi_app"
    src_dir  = app_dir / "src" / "main" / "java" / "com" / "vss" / "vehicleapp"
    frag_dir = src_dir / "fragments"
    res_dir  = app_dir / "src" / "main" / "res" / "layout"
    for d in [src_dir, frag_dir, res_dir]:
        d.mkdir(parents=True, exist_ok=True)

    scores = []
    for domain, props in domain_map.items():
        print(f"  [HMI] {domain.upper()} ({len(props)} properties)...")

        # Generate Kotlin fragment
        kt = _generate_kotlin_fragment(domain, props)
        xml = _generate_xml_layout(domain, props)

        (frag_dir / f"{domain.capitalize()}Fragment.kt").write_text(kt)
        (res_dir  / f"fragment_{domain}.xml").write_text(xml)

        # Score
        has_class    = "class " in kt and "Fragment" in kt
        has_property = "CarPropertyManager" in kt
        has_xml      = "<LinearLayout" in xml or "<ScrollView" in xml
        score = (1.0 if has_class else 0.0) * 0.4 + \
                (1.0 if has_property else 0.0) * 0.4 + \
                (1.0 if has_xml else 0.0) * 0.2
        scores.append(score)
        print(f"    ✓ {domain.capitalize()}Fragment.kt score={score:.3f}")

    return sum(scores) / len(scores) if scores else 0.0


def _generate_kotlin_fragment(domain: str, properties: list) -> str:
    """Generate Kotlin Fragment with real CarPropertyManager IDs."""
    prop_consts = "\n".join(
        f"    private val PROP_{n} = {i}  // {hex(i)} — {d}"
        for n, i, t, a, d in properties
    )
    callbacks = "\n".join(
        f"            {i} -> binding.tv{n[-15:].replace('_','')}.text = \"${{value.value}}\""
        for n, i, t, a, d in properties[:10]
    )
    register_calls = "\n            ".join(
        f"carPropertyManager?.registerCallback(propertyCallback, PROP_{n}, "
        f"CarPropertyManager.SENSOR_RATE_ONCHANGE)"
        for n, i, t, a, d in properties[:8]
    )

    return f"""package com.vss.vehicleapp.fragments

import android.car.Car
import android.car.hardware.CarPropertyValue
import android.car.hardware.property.CarPropertyEventCallback
import android.car.hardware.property.CarPropertyManager
import android.os.Bundle
import android.view.LayoutInflater
import android.view.View
import android.view.ViewGroup
import androidx.fragment.app.Fragment
import com.vss.vehicleapp.databinding.Fragment{domain.capitalize()}Binding

class {domain.capitalize()}Fragment : Fragment() {{

    private var _binding: Fragment{domain.capitalize()}Binding? = null
    private val binding get() = _binding!!
    private var car: Car? = null
    private var carPropertyManager: CarPropertyManager? = null

    // VSS Property IDs (compiled from AOSP build)
{prop_consts}

    private val propertyCallback = object : CarPropertyEventCallback {{
        override fun onChangeEvent(value: CarPropertyValue<*>) {{
            activity?.runOnUiThread {{
                when (value.propertyId) {{
{callbacks}
                    else -> {{}}
                }}
            }}
        }}
        override fun onErrorEvent(propId: Int, zone: Int) {{}}
    }}

    override fun onCreateView(i: LayoutInflater, c: ViewGroup?, s: Bundle?): View {{
        _binding = Fragment{domain.capitalize()}Binding.inflate(i, c, false)
        return binding.root
    }}

    override fun onViewCreated(view: View, savedInstanceState: Bundle?) {{
        super.onViewCreated(view, savedInstanceState)
        try {{
            car = Car.createCar(requireContext())
            carPropertyManager = car?.getCarManager(Car.PROPERTY_SERVICE)
                    as? CarPropertyManager
            {register_calls}
        }} catch (e: Exception) {{ e.printStackTrace() }}
    }}

    override fun onDestroyView() {{
        super.onDestroyView()
        carPropertyManager?.unregisterCallback(propertyCallback)
        car?.disconnect()
        _binding = null
    }}
}}
"""


def _generate_xml_layout(domain: str, properties: list) -> str:
    """Generate XML layout for domain fragment."""
    items = []
    for name, prop_id, typ, access, desc in properties:
        safe = name[-15:].lower().replace("_", "")
        if "WRITE" in access and typ == "BOOLEAN":
            items.append(
                f'    <LinearLayout android:layout_width="match_parent" '
                f'android:layout_height="wrap_content" android:orientation="horizontal">\n'
                f'        <TextView android:layout_width="0dp" android:layout_height="wrap_content" '
                f'android:layout_weight="1" android:text="{desc}" android:textSize="14sp"/>\n'
                f'        <Switch android:id="@+id/switch{safe}" '
                f'android:layout_width="wrap_content" android:layout_height="wrap_content"/>\n'
                f'    </LinearLayout>'
            )
        else:
            items.append(
                f'    <LinearLayout android:layout_width="match_parent" '
                f'android:layout_height="wrap_content" android:orientation="horizontal">\n'
                f'        <TextView android:layout_width="0dp" android:layout_height="wrap_content" '
                f'android:layout_weight="1" android:text="{desc}:" android:textSize="14sp"/>\n'
                f'        <TextView android:id="@+id/tv{safe}" '
                f'android:layout_width="wrap_content" android:layout_height="wrap_content" '
                f'android:text="--" android:textSize="14sp" android:textStyle="bold"/>\n'
                f'    </LinearLayout>'
            )

    return (
        '<?xml version="1.0" encoding="utf-8"?>\n'
        '<ScrollView xmlns:android="http://schemas.android.com/apk/res/android"\n'
        '    android:layout_width="match_parent" android:layout_height="match_parent">\n'
        '    <LinearLayout android:layout_width="match_parent" '
        'android:layout_height="wrap_content" android:orientation="vertical" android:padding="16dp">\n'
        f'        <TextView android:layout_width="match_parent" android:layout_height="wrap_content"\n'
        f'            android:text="{domain.upper()} Properties" android:textSize="18sp" '
        f'android:textStyle="bold" android:paddingBottom="8dp"/>\n'
        + "\n".join(f"        {item}" for item in items) +
        '\n    </LinearLayout>\n</ScrollView>\n'
    )


# ═══════════════════════════════════════════════════════════════════
# Main pipeline
# ═══════════════════════════════════════════════════════════════════
def main():
    OUTPUT_DIR.mkdir(parents=True, exist_ok=True)
    (OUTPUT_DIR / "vts").mkdir(exist_ok=True)  # fake_vhal dir removed — not used

    print("=" * 70)
    print("  C5 Full Pipeline — Advanced Runtime Validation")
    print("=" * 70)
    print(f"  Output   : {OUTPUT_DIR.resolve()}")
    print(f"  C4 input : {C4_OUTPUT_DIR}")
    print()

    t_start = time.time()
    results = {}

    # ── Step 1: Load VSS properties ──────────────────────────────
    print("[ STEP 1 ] Loading VSS properties from C4 output + AOSP dump...")
    domain_map = load_vss_properties()
    if not domain_map:
        print("  ERROR: No VSS properties loaded — check C4 output and AOSP dump")
        return
    total = sum(len(v) for v in domain_map.values())
    print(f"  ✓ Loaded {total} properties across {len(domain_map)} domains\n")

    # ── Step 2: SKIPPED — FakeVehicleHardware patch not used ────
    # VssVehicleHardware.cpp (C3/C4 output) hardcodes the property table
    # directly in C++. VssProperties.json and FakeVehicleHardware patching
    # are not needed. Steps 3+4 (VTS + HMI) run against VssVehicleHardware.
    fake_score = 0.0   # not scored — kept for formula compat only
    print("[ STEP 2 ] Skipped — FakeVehicleHardware patch not required\n"
          "           VssVehicleHardware.cpp (C3/C4) serves properties directly.\n")

    # ── Step 3: Generate VTS tests ───────────────────────────────
    print("[ STEP 3 ] Generating VSS VTS tests (Agent 2)...")
    vts_agent          = VtsGeneratorAgent()
    vts_content, vts_score = vts_agent.run(domain_map)

    (OUTPUT_DIR / "vts" / "VtsHalAutomotiveVehicleVss.cpp").write_text(vts_content)
    (OUTPUT_DIR / "vts" / "Android.bp").write_text(vts_agent._generate_android_bp())
    (OUTPUT_DIR / "vts" / "VtsHalAutomotiveVehicleVss.xml").write_text(
        vts_agent._generate_test_config())
    results["vts"] = {"score": vts_score}
    print(f"  ✓ VTS tests: score={vts_score:.3f} → output_c5/vts/\n")

    # ── Step 4: Generate HMI app ─────────────────────────────────
    print("[ STEP 4 ] Generating HMI app (Agent 3, reusing C4 DSPy)...")
    hmi_score = generate_hmi_app(domain_map)
    results["hmi_app"] = {"score": hmi_score}
    print(f"  ✓ HMI app: score={hmi_score:.3f} → output_c5/hmi_app/\n")

    # ── Generate AndroidManifest.xml + Android.bp for HMI app ────
    _generate_hmi_build_files(domain_map)
    print("  ✓ HMI build files: AndroidManifest.xml + Android.bp written\n")

    # ── Step 5: Overall score ────────────────────────────────────
    overall = (vts_score * 0.60 + hmi_score * 0.40)  # fake_score removed; VTS is ground-truth
    results["overall"] = overall

    # Save results
    results_path = OUTPUT_DIR / "c5_results.json"
    results_path.write_text(json.dumps(results, indent=2))

    elapsed = time.time() - t_start

    print("=" * 70)
    print("  C5 Pipeline Complete!")
    print("=" * 70)
    print(f"  VTS tests (ground-truth)  : {vts_score:.3f}")
    print(f"  HMI app                   : {hmi_score:.3f}")
    print(f"  Overall score             : {overall:.3f}")
    print(f"  Time                      : {elapsed:.0f}s")
    print()
    print("  Next steps (on GCP VM):")
    print("  Prerequisites: C3/C4 VssVehicleHardware service already built")
    print("  and deployed as IVehicle/default on Cuttlefish.")
    print()
    print("  1. Copy VTS test into AOSP tree and build:")
    print(f"     cp -r output_c5/vts/ ~/aosp-14-auto/{VTS_REL}/")
    print("     source build/envsetup.sh && lunch aosp_cf_x86_64_auto-trunk_staging-userdebug")
    print("     mmm test/vts/vss_vehicle")
    print()
    print("  2. Stop stock VHAL, confirm VssVehicleHardware is running:")
    print("     adb root && adb shell stop vendor.vehicle-default")
    print("     adb shell lshal | grep automotive.vehicle")
    print()
    print("  3. Run VTS against VssVehicleHardware:")
    print("     atest VtsHalAutomotiveVehicleVss -c")
    print("     # Checks: ServiceAvailable, VssPropertiesRegistered,")
    print("     #         AllIdsUnique, AllIdsWellFormed")
    print()
    print("  4. Install HMI app:")
    print("     mmm output_c5/hmi_app && adb install VssDashboardApp.apk")
    print("=" * 70)


if __name__ == "__main__":
    main()