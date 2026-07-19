# agents/rag_dspy_cpp_agent.py
import re
import dspy
from rag.aosp_retriever import AOSPRetriever
from agents.vss_glue_agent import _parse_aidl_properties
from dspy_opt.hal_signatures import (
    ModernCppVehicleHardwareSignature,
    CppSkeletonSignature,
    CppPropertyEntriesSignature,
    CppRegisterBodySignature,
    CppVehicleAssertions,
)

# Properties per chunk for the chunked generation path (skeleton +
# per-chunk property entries). Starting point derived from REAL token
# measurements: the truncated BODY output observed in production
# (73 entries before cutoff, ~4302 tokens under max_tokens=4096)
# measures out to ~59 tokens/entry. Two things eat into the
# max_tokens=4096 budget before any entries are even written:
#   1. ChainOfThought always generates a "reasoning" field BEFORE the
#      actual output (~100-200 tokens typical), which is NOT part of
#      the entries themselves but still counts against max_tokens.
#   2. Property name length varies a lot — some VSS names run well
#      past 100 characters (e.g.
#      VEHICLE_CHILDREN_CABIN_CHILDREN_SEAT_CHILDREN_ROW2_CHILDREN_
#      DRIVERSIDE_CHILDREN_SWITCH_CHILDREN_MASSAGE_CHILDREN_
#      ISDECREASEENGAGED), so 59 tok/entry is an AVERAGE, not a
#      worst-case bound, and a chunk landing on a run of long names
#      can blow past it.
#
# 40 already measured out to ~39% headroom after accounting for both
# of the above (no truncation has been observed at 40 in practice).
# Lowered to 30 anyway as a PREVENTIVE margin before a full pipeline
# run, not in response to an observed failure: 30 entries × ~59
# tok/entry ≈ 1770 tok for entries, leaving ~2180 tok (~53%) of
# headroom — the extra safety costs only ~1 additional LLM call per
# large domain (e.g. CABIN's 168 properties goes from 5 chunks to 6)
# against a full Colab run that takes tens of minutes, so the
# trade-off favors caution here. If this still proves insufficient in
# a real run, lower it further — this is a heuristic tuned against one
# real truncation case, not a hard guarantee for any LM/prompt
# combination.
#
# LOWERED from 30 → 12 after the real-HW-register-file contract shipped:
# each switch-case entry now emits ~8-10 lines (ifstream/ofstream open,
# type-specific read/write, error handling) versus ~3-4 lines under the
# previous in-memory-map contract. Domains at or below the OLD threshold
# (ADAS=24, HVAC=20, INFOTAINMENT=20 — all ≤30) were silently truncated
# mid-file (unbalanced braces, cut off mid-statement) because they never
# triggered chunking, while every domain >30 properties WAS chunked and
# came out complete. 12 was chosen so ALL current domains (min size 20)
# safely chunk under the new, more verbose per-property code.
CHUNK_SIZE = 12

# Maximum retry attempts per chunk when enable_chunk_retry=True.
# Only used by C4/C4-minimal callers — C3 passes enable_chunk_retry=False.
MAX_CHUNK_RETRIES = 5
_PLACEHOLDER = "/*__PROPERTY_ENTRIES_PLACEHOLDER__*/"

_MD_FENCE_RE = re.compile(r"^```[a-zA-Z]*\n?|```$", re.MULTILINE)


def _strip_markdown_fences(text: str) -> str:
    """LLMs occasionally wrap code output in markdown fences (```cpp ...
    ```) despite the contract explicitly forbidding it. Strip any
    leading/trailing fence so a single instance of this drift doesn't
    leave invalid C++ (a literal backtick fence) in the generated file.
    Only strips fences at the very start/end of the string, not
    backticks that might legitimately appear inside a string literal
    or comment mid-file.
    """
    if not text:
        return text
    stripped = text.strip()
    if stripped.startswith("```"):
        # Drop the opening fence line (``` or ```cpp etc.)
        first_newline = stripped.find("\n")
        stripped = stripped[first_newline + 1:] if first_newline != -1 else ""
    if stripped.rstrip().endswith("```"):
        stripped = stripped.rstrip()[:-3]
    return stripped.strip() + "\n"

# 4 vehicle-anchored queries — surface DefaultVehicleHal/FakeVehicleHardware
# above generic biometrics/bluetooth/sensors HALs in the corpus
_QUERIES = [
    "IVehicleHardware getAllPropertyConfigs getValues setValues "
    "registerOnPropertyChangeEvent DumpResult checkHealth",
    "GetValuesCallback GetValueRequest SetValuesCallback SetValueRequest "
    "VehiclePropValue automotive vehicle async",
    "DefaultVehicleHal AServiceManager_addService IVehicle main "
    "ndk SharedRefBase make automotive vehicle",
    "FakeVehicleHardware getAllPropertyConfigs VehiclePropConfig "
    "areaConfigs automotive vehicle implementation",
    "unordered_map mValues property store getValues setValues "
    "VehiclePropValue lock_guard mutex vehicle hardware",
]

# Injected into every generation — explicit AIDL V3 contract
_CONTRACT = """
=== Android 14 AIDL VHAL V3 contract — NO EXCEPTIONS ===
CRITICAL INCLUDES — ALWAYS ADD THESE AT THE TOP OF HEADER FILE:
#pragma once
#include <IVehicleHardware.h>
#include <VehicleHalTypes.h>
#include <vector>
#include <memory>
#include <android/log.h>
#include <aidl/android/hardware/automotive/vehicle/VehicleProperty.h>

NOTE ON INCLUDES: Use the UNIFIED VehicleProperty.h — NOT per-domain headers.
All custom VSS properties (ADAS, BODY, CABIN, etc.) are merged into the single
aidl_property/VehicleProperty.aidl on the build system, so they all live in the
VehicleProperty enum in VehicleProperty.h. Per-domain headers like
VehiclePropertyAdas.h or VehiclePropertyBody.h do NOT exist at build time.

NAMESPACE (VERY IMPORTANT - ALWAYS DO THIS):
- In .h file:
  namespace android::hardware::automotive::vehicle {
      class VehicleHalService{Domain} : public IVehicleHardware {
          ...
      };
  } // namespace android::hardware::automotive::vehicle

- In .cpp file:
  #include "VehicleHalService{Domain}.h"

  namespace android::hardware::automotive::vehicle {
  using namespace aidl::android::hardware::automotive::vehicle;

  // all method implementations here
  } // namespace android::hardware::automotive::vehicle

ARCHITECTURE:
  VehicleHalService{Domain} : public IVehicleHardware ← you implement this (domain-specific)
  e.g. VehicleHalServiceAdas, VehicleHalServiceBody, VehicleHalServiceCabin, etc.
  Each domain has its OWN class with a UNIQUE name — NEVER use VssVehicleHardware for domain classes.
  DefaultVehicleHal is the binder layer — do NOT subclass it.
CLASS NAMING CONVENTION:
  Domain ADAS → class VehicleHalServiceAdas : public IVehicleHardware
  Domain BODY → class VehicleHalServiceBody : public IVehicleHardware
  Domain CABIN → class VehicleHalServiceCabin : public IVehicleHardware
  Domain CHASSIS → class VehicleHalServiceChassis : public IVehicleHardware
  Domain HVAC → class VehicleHalServiceHvac : public IVehicleHardware
  Domain INFOTAINMENT → class VehicleHalServiceInfotainment : public IVehicleHardware
  Domain POWERTRAIN → class VehicleHalServicePowertrain : public IVehicleHardware
PROP IDs:
  Use enum constant names from the UNIFIED VehicleProperty enum.
  ALWAYS add this single include at the top of BOTH .h and .cpp files:
  #include <aidl/android/hardware/automotive/vehicle/VehicleProperty.h>
  Then use VehicleProperty:: enum prefix with static_cast:
  {.prop = static_cast<int32_t>(VehicleProperty::VEHICLE_CHILDREN_ADAS_CHILDREN_EBA_CHILDREN_ISENABLED),
   .access = VehiclePropertyAccess::READ_WRITE}
  The enum names come from the AIDL enum provided in the properties section.
  Do NOT use raw hex values like 0x1000 — those are 16-bit raw values, not valid VHAL prop IDs.
  Do NOT use placeholder IDs like 0x12345678.
  Do NOT use per-domain enum prefixes like VehiclePropertyAdas:: or VehiclePropertyBody::.
  ALL properties across ALL domains use VehicleProperty:: prefix.
HEADER FILE (VehicleHalService{Domain}.h):
  Declare class VehicleHalService{Domain} : public IVehicleHardware
  with getAllPropertyConfigs(), getValues(), setValues() etc.
IMPLEMENTATION FILE (VehicleHalService{Domain}.cpp):
  MUST start with:
  #include "VehicleHalService{Domain}.h"
 
  Then wrap ALL implementations in namespace:
  namespace android::hardware::automotive::vehicle {
  using namespace aidl::android::hardware::automotive::vehicle;
 
  std::vector<VehiclePropConfig> VehicleHalService{Domain}::getAllPropertyConfigs() const {
      return {
          {.prop = static_cast<int32_t>(VehicleProperty::VEHICLE_CHILDREN_...), ...},
      };
  }
  // ... other method implementations
  } // namespace android::hardware::automotive::vehicle
MANDATORY signatures — Android 14 IVehicleHardware API (NOT Android 13):
Android 14 uses function types defined in IVehicleHardware.h:
  using GetValuesCallback = std::function<void(std::vector<GetValueResult>)>;
  using SetValuesCallback = std::function<void(std::vector<SetValueResult>)>;
  using PropertyChangeCallback = std::function<void(std::vector<VehiclePropValue>)>;
  using PropertySetErrorCallback = std::function<void(std::vector<SetValueErrorEvent>)>;
CORRECT Android 14 method signatures:
  std::vector<VehiclePropConfig> getAllPropertyConfigs() const override;
  StatusCode getValues(
      std::shared_ptr<const GetValuesCallback> callback,
      const std::vector<GetValueRequest>& requests) const override;
  StatusCode setValues(
      std::shared_ptr<const SetValuesCallback> callback,
      const std::vector<SetValueRequest>& requests) override;
  StatusCode updateSampleRate(int32_t cookie, int32_t propId, float sampleRate) override;
  StatusCode subscribe(const SubscribeOptions& options) override;
  StatusCode unsubscribe(int32_t cookie, int32_t propId) override;
  DumpResult dump(const std::vector<std::string>& options) override;
  StatusCode checkHealth() override;
  void registerOnPropertyChangeEvent(
      std::unique_ptr<const PropertyChangeCallback> callback) override;
  void registerOnPropertySetErrorEvent(
      std::unique_ptr<const PropertySetErrorCallback> callback) override;

REAL HARDWARE-REGISTER FILE STORE — MANDATORY, DO NOT STUB, DO NOT USE A PLAIN MAP:
  getValues/setValues MUST NOT be `(*callback)({}); return StatusCode::OK;`.
  That discards every request and stores nothing — a set-then-get round trip
  would silently return nothing, which is wrong. THIS EXACT PATTERN — an
  empty vector passed to the callback while ignoring `requests` — IS A
  STUB AND WILL BE REJECTED, even for domains where generation is split
  into skeleton + per-property chunks: the skeleton pass MUST ALSO emit
  the real read/write logic below, not a placeholder to fill in later.

  VALUE FIELD NAMES — THE REAL AIDL VehiclePropValue.value (RawPropValues)
  TYPE HAS EXACTLY THESE FIELDS AND NO OTHERS:
    int32Values   (std::vector<int32_t>)   — use for INT32 AND BOOLEAN
                                              (true/false stored as 1/0 —
                                              there is NO separate bool
                                              array field in real AOSP)
    int64Values   (std::vector<int64_t>)   — use for INT64
    floatValues   (std::vector<float>)     — use for FLOAT
    byteValues    (std::vector<uint8_t>)   — use for BYTES
    stringValue   (std::string)            — use for STRING (not an array)
  `boolValues` and `booleanValues` DO NOT EXIST on this type — using
  either is a compile error. A BOOLEAN-typed property's true/false MUST
  go through int32Values (e.g. `in.value.int32Values = {v ? 1 : 0};`).

  Each property is backed by a REAL file under
  /data/vendor/vss_hw/{domain_lower}/<hex_prop_id>.reg — this simulates a
  hardware register interface via genuine file I/O, not an in-memory map.
  Header needs: #include <mutex>, #include <string>; private members:
    mutable std::mutex mLock;
    static constexpr const char* kHwRegisterDir = "/data/vendor/vss_hw/{domain_lower}/";
    std::string registerPath(int32_t propId) const;
    bool readRegister(int32_t propId, VehiclePropValue& out) const;
    bool writeRegister(int32_t propId, const VehiclePropValue& in) const;
  Impl needs: #include <fstream>, #include <sys/stat.h>, #include <cerrno>
  registerPath(): snprintf "%s%08x.reg" with kHwRegisterDir and propId.
  readRegister(): use a switch(propId) with one case per property this
  domain owns (matching the enum constants in getAllPropertyConfigs()).
  Each case opens registerPath(propId) with std::ifstream; if the file
  doesn't exist yet, return a zero/false default (file not created yet is
  normal on first boot) — still return true with a valid default value.
  writeRegister(): if (mkdir(kHwRegisterDir, 0770) != 0 && errno != EEXIST)
  return false; — then open registerPath(propId) with
  std::ofstream(..., std::ios::trunc) and write the value.
  getValues(): for each request, call readRegister(req.prop.prop, v); if it
  returns true, status = OK and prop = v; else status = INVALID_ARG. Collect
  into a vector and pass to the callback — never pass an empty vector when
  requests is non-empty.
  setValues(): for each request, call writeRegister(req.value.prop,
  req.value); status = OK if it returns true, else INVALID_ARG. Same
  callback pattern as getValues.
  This IS real file I/O against /data/vendor/vss_hw/ — that is the point.
  Do NOT substitute it with an in-memory std::unordered_map; the file-backed
  register interface is what justifies this domain's SELinux permissions
  (see the SELinux agent's contract, which grants vss_hw_data_file access).

FORBIDDEN Android 13 types (do NOT use):
  IOnPropertyChangeCallback, IOnPropertySetErrorCallback — Android 13 only
  VssVehicleHardwareImpl — does not exist in Android 14
FORBIDDEN:
  #include <hidl/> — HIDL headers
  #include <ndk/ScopedAStatus.h> — wrong path, use <android/binder_status.h>
  #include <binder/AServiceManager.h> — wrong, use <android/binder_manager.h>
  #include <aidl/android/hardware/automotive/vehicle/IVehicleHardware.h> — wrong path, use <IVehicleHardware.h>
  #include <aidl/android/hardware/automotive/vehicle/DefaultVehicleHal.h> — wrong path, use <DefaultVehicleHal.h>
  #include <aidl/android/hardware/automotive/vehicle/VehiclePropertyAdas.h> — does NOT exist, use VehicleProperty.h
  #include <aidl/android/hardware/automotive/vehicle/VehiclePropertyBody.h> — does NOT exist, use VehicleProperty.h
  VehiclePropertyAdas:: | VehiclePropertyBody:: | VehiclePropertyCabin:: — use VehicleProperty:: instead
  HIDL_FETCH_* | Return<> | BnVehicle | BnIVehicle | .valueType | sync getValues
  aidlvhal:: prefix — use namespace directly via using namespace
  getValues/setValues that call the callback with an empty vector and
  discard the request — this is a stub, not a real property store
NEVER wrap output in markdown code fences (``` or ```cpp) — emit raw C++ only.
Start every .h file with the includes above. Do not omit them.
"""


class RagDspyCppAgent:
    def __init__(self, retriever=None, rag_db_path="rag/chroma_db", rag_top_k=8, **kwargs):
        self.retriever  = retriever or AOSPRetriever(db_path=rag_db_path)
        self.top_k      = rag_top_k
        self.predictor  = dspy.ChainOfThought(ModernCppVehicleHardwareSignature)
        self.skeleton_predictor = dspy.ChainOfThought(CppSkeletonSignature)
        self.entries_predictor  = dspy.ChainOfThought(CppPropertyEntriesSignature)
        self.register_body_predictor = dspy.ChainOfThought(CppRegisterBodySignature)
        self.assertions = CppVehicleAssertions(strict=False)

    def _retrieved_context(self) -> str:
        retrieved = self.retriever.retrieve_multi(_QUERIES, agent_type="cpp",
                                                   top_k=self.top_k)
        return "\n\n".join(
            doc.get("text", doc.get("page_content", "")) for doc in retrieved
        )

    def generate(
        self,
        domain:        str,
        properties:    str,
        extra_context: str = "",
    ) -> dict:
        retrieved_text = self._retrieved_context()

        # Always combine: contract + RAG + any repair/extra context
        # Never use "or" — repair must have BOTH violation feedback AND RAG grounding
        aosp_context = _CONTRACT + "\n" + retrieved_text
        if extra_context:
            aosp_context = extra_context + "\n" + aosp_context

        result = self.predictor(
            domain       = domain,
            properties   = properties,
            aosp_context = aosp_context,
        )
        result.cpp_header = _strip_markdown_fences(getattr(result, "cpp_header", "") or "")
        result.cpp_impl = _strip_markdown_fences(getattr(result, "cpp_impl", "") or "")
        result = self.assertions(result)

        return {
            "header":       getattr(result, "cpp_header",   "") or "",
            "impl":         getattr(result, "cpp_impl",     "") or "",
            "main_service": getattr(result, "main_service", "") or "",
            "android_bp":   getattr(result, "android_bp",   "") or "",
            "reasoning":    getattr(result, "reasoning",    "") or "",
            "violations":   getattr(result, "violations",   []),
        }

    def _build_chunk_properties_text(self, domain: str, chunk: list, aidl_block: str = "") -> str:
        """Build a compact per-chunk property spec, same format
        ModuleSpec.to_llm_spec() uses, so CppPropertyEntriesSignature
        sees the same shape of input as the unchunked path — plus the
        real AIDL enum block so CppVehicleAssertions' hallucination
        cross-check (applied after merging, see generate_chunked) has
        ground truth to check entries against.
        """
        lines = [f"HAL Domain: {domain}", f"Properties in this chunk: {len(chunk)}", ""]
        for prop in chunk:
            name   = getattr(prop, "id", "UNKNOWN")
            typ    = getattr(prop, "type", "UNKNOWN")
            access = getattr(prop, "access", "READ_WRITE")
            lines += [f"- Name: {name}", f"  Type: {typ}", f"  Access: {access}", ""]
        text = "\n".join(lines)
        if aidl_block:
            text += "\n" + aidl_block
        return text

    def _call_predictor_safely(self, predictor, **kwargs):
        """Call a DSPy predictor (entries_predictor / register_body_predictor)
        and return its result, or None if the call raised an exception.

        Observed in production: a generation run into a degenerate
        repetition loop (the LM repeated the same ~15 cases dozens of
        times, some with a doubled '_CHILDREN_CHILDREN_' prefix variant)
        and never reached the JSON's closing fields before exhausting its
        output budget -- DSPy's JSONAdapter then raised "failed to parse
        the LM response" / "Expected ... [reasoning, read_cases,
        write_cases] ... Actual ... [reasoning, read_cases]". That
        exception propagated all the way out of generate_chunked(),
        uncaught, past every retry mechanism in this file, and was only
        ever caught by rag_dspy_architect_agent.py's outermost per-
        sub-agent try/except -- which has ZERO retry, just logs "CPP →
        FAILED" and moves on, silently dropping the ENTIRE domain's CPP
        output (0 attempts recorded downstream, not even 1).

        Converting the exception to None here lets it flow into the
        SAME existing missing-entries / missing-case retry loops that
        already exist for the "generated too few names" case -- a
        parse failure is treated identically to "produced 0 usable
        results this attempt", which those loops are already built to
        retry, rather than requiring a second, parallel retry mechanism.
        """
        try:
            return predictor(**kwargs)
        except Exception as e:
            print(f"    ⚠ predictor call raised {type(e).__name__}: "
                  f"{str(e)[:200]} — treating as empty result for this attempt")
            return None

    def generate_chunked(
        self,
        domain:        str,
        prop_list:     list,
        aidl_block:    str = "",
        extra_context: str = "",
        chunk_size:    int = CHUNK_SIZE,
        enable_chunk_retry: bool = False,
        aidl_dir:      str = "",
        previous_full_code: str = "",
    ) -> dict:
        """Chunked counterpart to generate(), for domains with more
        properties than reliably fit in one LLM call without hitting
        max_tokens truncation (see CHUNK_SIZE above). Two passes:

          1. Skeleton: full header + every method except the body of
             getAllPropertyConfigs(), which contains a placeholder
             marker instead of real entries. This call's size is
             independent of property count, so it never truncates.

          2. Entries: for each chunk of `prop_list`, ask the LM for
             ONLY that chunk's initializer-list entries (no method
             wrapper, no class, no namespace) — small, bounded output
             per call regardless of total domain size.

        The chunks are concatenated and spliced into the skeleton's
        placeholder, then run through the same CppVehicleAssertions
        pass generate() uses, so the final result has identical
        structural guarantees (pragma once, self-include, namespace,
        AIDL include, hallucination cross-check) regardless of which
        path produced it.

        enable_chunk_retry: if True, retry chunks that produce fewer
        entries than expected. MUST be False for C3 (RAG+DSPy without
        feedback) — retry is a C4 contribution and enabling it in C3
        would blur the ablation boundary. Set True only from C4/C4-minimal
        callers.
        """
        # If the real .aidl file already exists (AIDL generation runs
        # before CPP in the pipeline), override prop_list's names with
        # what's ACTUALLY in that file -- the same principle already
        # applied to C5's kVssPropertyIds (_parse_aidl_properties is
        # the proven, single source of truth). Without this, prop_list
        # carries whatever name Python happened to track separately,
        # and CPP generation (entries_predictor, register_body_predictor)
        # ends up inventing its OWN version of a long compound name
        # independently -- the actual root cause behind every naming-
        # mismatch bug found in this pipeline (Hvac/Chassis compile
        # failures from getAllPropertyConfigs vs readRegister/
        # writeRegister disagreeing on one property's name; BODY's
        # register-body chunk repeatedly failing to reproduce a name
        # matching entries_predictor's own independently-typed version).
        if not aidl_dir:
            print(f"  [CPP DEBUG] {domain}: aidl_dir is empty/not passed — override SKIPPED entirely, "
                  f"using prop_list's own names (the original bug path)")
        real_aidl_names_set = set()
        if aidl_dir:
            import os as _os_diag
            _file_exists = _os_diag.path.exists(_os_diag.path.join(aidl_dir, f"VehicleProperty{domain.capitalize()}.aidl"))
            print(f"  [CPP DEBUG] {domain}: aidl_dir={aidl_dir!r} "
                  f"file_exists={_file_exists} prop_list_count={len(prop_list)}")
            # Scope to THIS module's specific .aidl file (e.g.
            # VehiclePropertyBody.aidl) — NOT a prefix filter on the
            # enum constant name. A property module_planner assigned to
            # the BODY module can have a VSS path starting with
            # "Cabin.Seat...", producing an enum name starting with
            # VEHICLE_CHILDREN_CABIN_CHILDREN_... — module grouping
            # (functional) and VSS tree path (structural) are different
            # classifications and do NOT necessarily share a prefix.
            # Confirmed by testing: filtering by name prefix incorrectly
            # excluded a real property that DOES belong in this file.
            real_props = _parse_aidl_properties(
                aidl_dir, file_pattern=f"VehicleProperty{domain.capitalize()}.aidl"
            )
            real_names_for_domain = [p["name"] for p in real_props]
            real_aidl_names_set = set(real_names_for_domain)
            print(f"  [CPP DEBUG] {domain}: real_names_for_domain_count={len(real_names_for_domain)}")
            if len(real_names_for_domain) == len(prop_list):
                class _RenamedProp:
                    def __init__(self, real_name, orig):
                        self.id = real_name
                        self.name = real_name
                        self.signal_name = real_name
                        self.type = getattr(orig, "type", "INT")
                        self.access = getattr(orig, "access", "READ_WRITE")
                prop_list = [_RenamedProp(rn, p) for rn, p in zip(real_names_for_domain, prop_list)]
                print(f"  [CPP DEBUG] {domain}: override APPLIED — prop_list renamed to real AIDL names")
            else:
                print(f"  [CPP] warning {domain}: real AIDL has {len(real_names_for_domain)} "
                      f"properties matching this domain's prefix but prop_list has "
                      f"{len(prop_list)} -- counts don't match, falling back to "
                      f"prop_list's own names")

        retrieved_text = self._retrieved_context()
        aosp_context = _CONTRACT + "\n" + retrieved_text
        if extra_context:
            aosp_context = extra_context + "\n" + aosp_context

        skeleton = self.skeleton_predictor(
            domain         = domain,
            property_count = str(len(prop_list)),
            aosp_context   = aosp_context,
        )
        header = _strip_markdown_fences(getattr(skeleton, "cpp_header", "") or "")
        impl   = _strip_markdown_fences(getattr(skeleton, "cpp_impl", "") or "")

        chunk_retry_count = 0  # total chunks that needed at least 1 retry, across entries + register bodies
        all_entries = []
        chunk_entry_names = []  # per-chunk: the ACTUAL names entries_predictor generated —
                                  # ground truth for register-body filtering below, since
                                  # entries_predictor and register_body_predictor are two
                                  # independent LLM calls that can render the same long,
                                  # deeply-nested property name differently (confirmed: one
                                  # occasionally drops or adds a duplicated "CHILDREN_"
                                  # segment relative to the other, producing a .cpp file
                                  # whose getAllPropertyConfigs() and readRegister()/
                                  # writeRegister() reference DIFFERENT enum name strings
                                  # for what's meant to be the same property — a compile
                                  # error, since only ONE of the two variants exists in the
                                  # real generated .aidl enum).
        for i in range(0, len(prop_list), chunk_size):
            chunk = prop_list[i:i + chunk_size]
            chunk_num = i // chunk_size + 1
            total_chunks = (len(prop_list) + chunk_size - 1) // chunk_size
            chunk_properties_text = self._build_chunk_properties_text(domain, chunk, aidl_block)

            chunk_entries = ""
            if enable_chunk_retry:
                for attempt in range(1, MAX_CHUNK_RETRIES + 2):
                    entries_result = self._call_predictor_safely(
                        self.entries_predictor,
                        domain       = domain,
                        properties   = chunk_properties_text,
                        aosp_context = aosp_context,
                    )
                    raw = _strip_markdown_fences(getattr(entries_result, "entries", "") or "") if entries_result else ""
                    generated_count = raw.count("static_cast<int32_t>")
                    expected_count  = len(chunk)

                    if generated_count >= expected_count:
                        chunk_entries = raw
                        if attempt > 1:
                            chunk_retry_count += 1
                            print(f"  [CPP chunk {chunk_num}/{total_chunks}] "
                                  f"✓ retry {attempt-1} recovered {generated_count}/{expected_count} entries")
                        break

                    if attempt <= MAX_CHUNK_RETRIES:
                        import re as _re
                        generated_names = set(_re.findall(r"VehicleProperty::(\w+)", raw))
                        expected_names  = {
                            (getattr(p, "name", None) or getattr(p, "signal_name", None) or str(p))
                            for p in chunk
                        }
                        missing_names = sorted(expected_names - generated_names)
                        missing_hint  = "\n".join(f"  - {n}" for n in missing_names[:10])
                        retry_context = (
                            f"=== RETRY: chunk {chunk_num}/{total_chunks} — "
                            f"MISSING {expected_count - generated_count} "
                            f"of {expected_count} entries ===\n"
                            f"You produced {generated_count} entries but MUST produce "
                            f"EXACTLY {expected_count}. These names appear to be missing:\n"
                            f"{missing_hint}\n"
                            f"Output ALL {expected_count} entries — do not skip any.\n"
                            f"=== END RETRY ==="
                        )
                        chunk_properties_text = retry_context + "\n\n" + chunk_properties_text
                        print(f"  [CPP chunk {chunk_num}/{total_chunks}] "
                              f"⚠ attempt {attempt}: {generated_count}/{expected_count} entries — retrying...")
                    else:
                        chunk_retry_count += 1  # attempted but ultimately failed — see the
                                                  # matching comment in the register-body loop below
                        print(f"  [CPP chunk {chunk_num}/{total_chunks}] "
                              f"✗ gave up after {MAX_CHUNK_RETRIES} retries: "
                              f"{generated_count}/{expected_count} entries")
                        chunk_entries = raw
            else:
                # C3 path: no retry — single shot per chunk, by design.
                entries_result = self._call_predictor_safely(
                    self.entries_predictor,
                    domain       = domain,
                    properties   = chunk_properties_text,
                    aosp_context = aosp_context,
                )
                chunk_entries = _strip_markdown_fences(getattr(entries_result, "entries", "") or "") if entries_result else ""

            all_entries.append(chunk_entries.strip())
            import re as _re2
            extracted_names = _re2.findall(r"VehicleProperty::(\w+)", chunk_entries)

            # Detect mismatches against the real AIDL enum (deterministic
            # check, same real_aidl_names_set the override above computed
            # — this is a lookup, not a guess). The FIX itself stays
            # LLM-based: mirrors register-body's established narrow-retry
            # pattern below — ask entries_predictor to regenerate JUST the
            # mismatched entries, explicitly showing it the correct AIDL
            # name to copy, rather than deterministically substituting it.
            #
            # Why detection-then-LLM-retry instead of the override alone:
            # the override sets prop_list[j].id to the real name BEFORE
            # this call, but entries_predictor is a free-text LLM
            # transcribing long (8-10 segment) nested names and can still
            # mistranscribe despite being shown the correct value. This
            # narrow retry gives it a focused second attempt at exactly
            # the entries that came out wrong — same mechanism already
            # proven to work for register-body's missing-case recovery.
            if real_aidl_names_set and enable_chunk_retry:
                mismatched = [
                    (j, extracted, chunk[j])
                    for j, extracted in enumerate(extracted_names)
                    if extracted not in real_aidl_names_set and j < len(chunk)
                    and getattr(chunk[j], "id", None) in real_aidl_names_set
                ]
                if mismatched:
                    print(f"  [CPP chunk {chunk_num}/{total_chunks}] "
                          f"⚠ entries mismatch: {len(mismatched)} name(s) don't match "
                          f"real AIDL — narrow retry for: "
                          f"{[m[1] for m in mismatched[:3]]}"
                          f"{'...' if len(mismatched) > 3 else ''}")
                    fix_lines = [f"HAL Domain: {domain}",
                                 f"Properties in this chunk: {len(mismatched)}", ""]
                    for _, wrong, prop in mismatched:
                        correct = prop.id
                        typ    = getattr(prop, "type", "UNKNOWN")
                        access = getattr(prop, "access", "READ_WRITE")
                        fix_lines += [
                            f"- Name: {correct}", f"  Type: {typ}",
                            f"  Access: {access}", "",
                        ]
                    fix_text = "\n".join(fix_lines)
                    if aidl_block:
                        fix_text += "\n" + aidl_block
                    fix_result = self.entries_predictor(
                        domain       = domain,
                        properties   = fix_text,
                        aosp_context = aosp_context,
                    )
                    fix_raw = _strip_markdown_fences(getattr(fix_result, "entries", "") or "")
                    fix_names = _re2.findall(r"VehicleProperty::(\w+)", fix_raw)
                    recovered = 0
                    for (j, wrong, prop), new_name in zip(mismatched, fix_names):
                        if new_name in real_aidl_names_set:
                            # splice: replace the old (wrong) block's name
                            # reference with the newly-generated, verified
                            # entry's block — keep whatever access mode the
                            # ORIGINAL entry had (structure), only the name
                            # comes from the retry.
                            chunk_entries = chunk_entries.replace(wrong, new_name, 1)
                            extracted_names[j] = new_name
                            recovered += 1
                    if recovered:
                        all_entries[-1] = chunk_entries.strip()
                        print(f"  [CPP chunk {chunk_num}/{total_chunks}] "
                              f"✓ narrow retry recovered {recovered}/{len(mismatched)} "
                              f"correct name(s)")
                    else:
                        print(f"  [CPP chunk {chunk_num}/{total_chunks}] "
                              f"✗ narrow retry did not produce a matching AIDL name — "
                              f"keeping original (will surface via post-validation)")

            chunk_entry_names.append(extracted_names)  # ORDERED list

        merged_entries = "\n".join(all_entries)

        if _PLACEHOLDER in impl:
            impl = impl.replace(_PLACEHOLDER, merged_entries, 1)
        else:
            # Skeleton dropped the placeholder despite instructions —
            # fall back to inserting entries right after the opening
            # `return {` of getAllPropertyConfigs() so generation can
            # still proceed rather than silently losing all entries.
            return_brace_re = re.compile(
                r"(getAllPropertyConfigs\(\)\s*const\s*\{\s*return\s*\{)"
            )
            if return_brace_re.search(impl):
                impl = return_brace_re.sub(r"\1\n" + merged_entries, impl, count=1)
            else:
                impl += f"\n// WARNING: could not locate insertion point — entries appended raw:\n{merged_entries}\n"

        # readRegister/writeRegister are declared in the skeleton header
        # (private members of every VehicleHalService{Domain}) but their
        # BODIES are never generated by any LLM call above — the skeleton
        # only writes getValues/setValues (which CALL them) and entries
        # only cover getAllPropertyConfigs(). Left unaddressed, this is a
        # readRegister/writeRegister are declared in the skeleton header
        # (private members of every VehicleHalService{Domain}) and the
        # skeleton's switch/default wrapper for both already exists, with
        # __READ_CASES_PLACEHOLDER__/__WRITE_CASES_PLACEHOLDER__ markers.
        # Generated here via the SAME chunked-LLM approach as entries
        # above (CppRegisterBodySignature, bounded per-chunk output) —
        # this keeps the artifact genuinely LLM-generated (the object of
        # study in this thesis), while chunking removes the domain-size
        # reliability risk a single large call would have.
        def _filter_cases_to_allowed_names(cases_text: str, allowed_names: set) -> tuple[str, set]:
            """Keep only `case static_cast<int32_t>(VehicleProperty::NAME): { ... }`
            blocks where NAME is in allowed_names — strips any case the LLM
            added for a property outside its assigned chunk (observed:
            property names bleeding in from aosp_context's real AOSP
            examples, e.g. a Body-domain chunk emitting an ADAS or OBD
            property's case). Brace-counts rather than using a single
            regex, since a case body can contain nested `{ }` (e.g. the
            `if (!f.good()) { ... }` inside every generated case).
            Returns (filtered_text, set of names actually kept).
            """
            kept_blocks, kept_names = [], set()
            for m in re.finditer(r"case\s+static_cast<int32_t>\(VehicleProperty::(\w+)\)\s*:\s*\{", cases_text):
                name = m.group(1)
                if name not in allowed_names:
                    continue
                depth = 1
                pos = m.end()
                while depth > 0 and pos < len(cases_text):
                    if cases_text[pos] == "{":
                        depth += 1
                    elif cases_text[pos] == "}":
                        depth -= 1
                    pos += 1
                if name in kept_names:
                    # The LLM repeated a case for this property WITHIN
                    # this single generation call (observed with very
                    # long, deeply-nested names — e.g. Body's ISUPENGAGED
                    # case appearing twice in one readRegister() output).
                    # kept_names (a set) would silently dedupe this match
                    # with no visible effect, masking that kept_blocks (a
                    # list) was about to carry the duplicate straight
                    # into the final switch statement — a real compile-
                    # blocking "duplicate case value" error that the
                    # missing-case retry loop above never catches, since
                    # it only checks case coverage (this name IS
                    # present, just twice), never per-name uniqueness.
                    # Skip every repeat, keep the first occurrence only.
                    continue
                kept_blocks.append(cases_text[m.start():pos])
                kept_names.add(name)
            return "\n".join(kept_blocks), kept_names

        # ── Surgical retry: determine which chunk(s) an error actually
        # belongs to, so a retry can reuse every OTHER chunk's already-
        # working code verbatim instead of re-rolling the whole domain.
        #
        # Observed directly in production (CABIN, 3 full-domain retry
        # attempts): attempt 1 failed on a duplicate case in property A;
        # attempt 2 fixed A but introduced an unrelated wrong-field-name
        # error in property B; attempt 3 fixed B but reintroduced a
        # duplicate case for property C. Each attempt re-rolls ALL 14
        # chunks, so fixing one chunk's problem does nothing to prevent
        # an unrelated chunk from independently failing differently on
        # the same attempt — probabilistic, not surgical.
        #
        # This extracts every quoted identifier from extra_context (the
        # error feedback text — see _generate()'s error_marker parsing)
        # and maps each one to the chunk index whose property slice
        # contains it. Chunks with NO reported error are left out of
        # chunks_needing_regen entirely and, if previous_full_code is
        # available, get their cases copied forward unchanged rather
        # than regenerated.
        chunks_needing_regen: set[int] = set()
        if previous_full_code and extra_context:
            reported_names = set(re.findall(r"'([A-Z][A-Z0-9_]*)'", extra_context))
            if reported_names:
                for i in range(0, len(prop_list), chunk_size):
                    chunk_idx = i // chunk_size
                    chunk_prop_names = {
                        getattr(p, "name", None) or getattr(p, "signal_name", None) or str(p)
                        for p in prop_list[i:i + chunk_size]
                    }
                    if chunk_prop_names & reported_names:
                        chunks_needing_regen.add(chunk_idx)
                if chunks_needing_regen:
                    total_chunks_for_log = (len(prop_list) + chunk_size - 1) // chunk_size
                    print(f"  [CPP surgical retry] {domain}: error(s) traced to "
                          f"{len(chunks_needing_regen)}/{total_chunks_for_log} chunk(s) "
                          f"{sorted(chunks_needing_regen)} — reusing all other chunks' "
                          f"previous output unchanged")
                else:
                    # Couldn't map any reported name to a chunk (e.g. the
                    # error references something outside this domain, or
                    # a generic clang error with no quoted identifier) —
                    # safe fallback: regenerate everything, exactly as
                    # before this fix existed.
                    print(f"  [CPP surgical retry] {domain}: could not map reported "
                          f"error(s) to a specific chunk — falling back to full regenerate")

        def _reuse_chunk_cases(chunk_props) -> tuple[str, str]:
            """Extract this chunk's read/write cases from previous_full_code
            by property-NAME membership (not by text position — chunk
            boundaries aren't preserved in the final merged/spliced
            output), reusing the same case-block-with-brace-matching scan
            _filter_cases_to_allowed_names already uses elsewhere."""
            names = {
                getattr(p, "name", None) or getattr(p, "signal_name", None) or str(p)
                for p in chunk_props
            }
            rw_names = {
                (getattr(p, "name", None) or getattr(p, "signal_name", None) or str(p))
                for p in chunk_props
                if str(getattr(p, "access", "")).upper() in ("READ_WRITE", "WRITE")
            }
            read_text, _  = _filter_cases_to_allowed_names(previous_full_code, names)
            write_text, _ = _filter_cases_to_allowed_names(previous_full_code, rw_names)
            return read_text, write_text

        all_read_cases, all_write_cases = [], []
        for i in range(0, len(prop_list), chunk_size):
            chunk = prop_list[i:i + chunk_size]
            chunk_idx = i // chunk_size

            if previous_full_code and chunks_needing_regen and chunk_idx not in chunks_needing_regen:
                reused_read, reused_write = _reuse_chunk_cases(chunk)
                all_read_cases.append(reused_read.strip())
                all_write_cases.append(reused_write.strip())
                continue

            chunk_properties_text = self._build_chunk_properties_text(domain, chunk, aidl_block)

            # Ground truth for names: what entries_predictor ACTUALLY wrote
            # for this chunk, not Python's clean prop_list names — see the
            # chunk_entry_names docstring above for why these can differ.
            # Paired positionally (both lists follow the same input order);
            # if entries generated fewer names than expected (a residual
            # gap even after that loop's own retries), zip() safely
            # truncates rather than index-erroring, and access-mode
            # pairing for the missing tail is simply skipped.
            real_names_ordered = chunk_entry_names[chunk_idx] if chunk_idx < len(chunk_entry_names) else []
            if len(real_names_ordered) != len(chunk):
                print(f"  [CPP register-body chunk {chunk_idx + 1}] ⚠ entries produced "
                      f"{len(real_names_ordered)} names but chunk has {len(chunk)} properties — "
                      f"pairing by position for the overlapping range only")

            chunk_names = set(real_names_ordered)
            chunk_rw_names = {
                real_name
                for real_name, p in zip(real_names_ordered, chunk)
                if str(getattr(p, "access", "")).upper() in ("READ_WRITE", "WRITE")
            }

            name_to_prop = dict(zip(real_names_ordered, chunk))  # for building narrow retry requests

            read_cases_text, write_cases_text = "", ""
            if enable_chunk_retry:
                kept_read_cases_accum, kept_write_cases_accum = "", ""
                kept_read_names_accum, kept_write_names_accum = set(), set()
                retry_properties_text = chunk_properties_text

                for attempt in range(1, MAX_CHUNK_RETRIES + 2):
                    body_result = self._call_predictor_safely(
                        self.register_body_predictor,
                        domain       = domain,
                        properties   = retry_properties_text,
                        aosp_context = aosp_context,
                    )
                    raw_read  = _strip_markdown_fences(getattr(body_result, "read_cases", "") or "") if body_result else ""
                    raw_write = _strip_markdown_fences(getattr(body_result, "write_cases", "") or "") if body_result else ""
                    new_read_text,  new_read_names  = _filter_cases_to_allowed_names(raw_read, chunk_names)
                    new_write_text, new_write_names = _filter_cases_to_allowed_names(raw_write, chunk_rw_names)

                    # Merge newly-generated cases with whatever prior attempts
                    # already got right — a narrow retry regenerating just 1
                    # property must not discard the other 11 that were
                    # already correct on attempt 1.
                    if new_read_text.strip():
                        kept_read_cases_accum += ("\n" if kept_read_cases_accum else "") + new_read_text
                        kept_read_names_accum |= new_read_names
                    if new_write_text.strip():
                        kept_write_cases_accum += ("\n" if kept_write_cases_accum else "") + new_write_text
                        kept_write_names_accum |= new_write_names

                    read_cases_text, write_cases_text = kept_read_cases_accum, kept_write_cases_accum

                    if kept_read_names_accum >= chunk_names and kept_write_names_accum >= chunk_rw_names:
                        if attempt > 1:
                            chunk_retry_count += 1
                            print(f"  [CPP register-body chunk {i // chunk_size + 1}] "
                                  f"✓ retry {attempt-1} recovered all cases")
                        break

                    if attempt <= MAX_CHUNK_RETRIES:
                        missing = sorted((chunk_names - kept_read_names_accum) | (chunk_rw_names - kept_write_names_accum))
                        # Narrow retry (only the missing properties, not the
                        # full chunk) — cheaper and normally sufficient once
                        # it's actually given the correct required name (see
                        # the CRITICAL note below — a real bug, previously
                        # attributed to LLM "auto-correction" behavior,
                        # actually caused every narrow retry to silently
                        # request the WRONG name every time). Kept the
                        # narrow-then-full-chunk fallback structure anyway
                        # as defense in depth, in case some other class of
                        # miss genuinely benefits from full-chunk context.
                        missing_props = [(n, name_to_prop[n]) for n in missing if n in name_to_prop]
                        use_narrow = missing_props and attempt <= 2
                        if use_narrow:
                            # CRITICAL: use the REQUIRED name (n, from
                            # entries' real output / chunk_names) — NOT
                            # prop.id (the prop object's own "clean" name).
                            # This was the actual bug behind every narrow
                            # retry failing identically: _build_chunk_
                            # properties_text always rendered prop.id,
                            # silently asking the LLM for a DIFFERENT
                            # (clean) name than the one being checked
                            # against — the LLM correctly wrote what it
                            # was asked for, every time; it was never
                            # asked for the name that was actually needed.
                            lines = [f"HAL Domain: {domain}", f"Properties in this chunk: {len(missing_props)}", ""]
                            for required_name, prop in missing_props:
                                typ    = getattr(prop, "type", "UNKNOWN")
                                access = getattr(prop, "access", "READ_WRITE")
                                lines += [f"- Name: {required_name}", f"  Type: {typ}", f"  Access: {access}", ""]
                            retry_properties_text = "\n".join(lines)
                            if aidl_block:
                                retry_properties_text += "\n" + aidl_block
                            print(f"  [CPP register-body chunk {i // chunk_size + 1}] "
                                  f"⚠ attempt {attempt}: missing {len(missing)} case(s) "
                                  f"— retrying narrowly for: {missing[:3]}{'...' if len(missing) > 3 else ''}")
                        else:
                            retry_properties_text = chunk_properties_text
                            print(f"  [CPP register-body chunk {i // chunk_size + 1}] "
                                  f"⚠ attempt {attempt}: missing {len(missing)} case(s) "
                                  f"— narrow retry exhausted, falling back to full-chunk "
                                  f"retry for: {missing[:3]}{'...' if len(missing) > 3 else ''}")
                    else:
                        chunk_retry_count += 1  # attempted retries even though they ultimately failed —
                                                  # previously only successful recoveries were counted,
                                                  # so a chunk that exhausted all retries WITHOUT
                                                  # succeeding silently contributed 0, making the
                                                  # final "cpp_chunk_retries=0" summary line say
                                                  # "no retry activity" when 5 retries had in fact
                                                  # just failed — exactly backwards from useful.
                        print(f"  [CPP register-body chunk {i // chunk_size + 1}] "
                              f"✗ gave up after {MAX_CHUNK_RETRIES} retries: "
                              f"still missing {len((chunk_names - kept_read_names_accum) | (chunk_rw_names - kept_write_names_accum))}")
            else:
                # C3 path: no retry — single shot per chunk, by design.
                body_result = self._call_predictor_safely(
                    self.register_body_predictor,
                    domain       = domain,
                    properties   = chunk_properties_text,
                    aosp_context = aosp_context,
                )
                raw_read  = _strip_markdown_fences(getattr(body_result, "read_cases", "") or "") if body_result else ""
                raw_write = _strip_markdown_fences(getattr(body_result, "write_cases", "") or "") if body_result else ""
                read_cases_text, _  = _filter_cases_to_allowed_names(raw_read, chunk_names)
                write_cases_text, _ = _filter_cases_to_allowed_names(raw_write, chunk_rw_names)

            all_read_cases.append(read_cases_text.strip())
            all_write_cases.append(write_cases_text.strip())

        def _dedup_across_chunks(cases_text: str) -> str:
            """Remove duplicate `case` blocks that survived per-chunk dedup
            because they came from DIFFERENT, independent chunk-generation
            calls — e.g. chunk covering properties 1-14 and chunk covering
            properties 141-154 each independently emitting a case for the
            SAME property name (observed directly: a name appearing 3
            times across widely separated line ranges in one real Cabin
            output, ~1000 lines apart — clearly 3 separate LLM calls, not
            one call repeating itself).

            _filter_cases_to_allowed_names' `if name in kept_names`
            dedup only ever sees ONE chunk's raw text per call — kept_names
            is reinitialised fresh every call, so it structurally cannot
            detect a name that chunk A and chunk B both (correctly, from
            each chunk's own local perspective) produced. This runs ONCE,
            here, on the FULLY MERGED text after every chunk has already
            contributed its cases — the only point where the complete
            picture actually exists.
            """
            kept_blocks, kept_names = [], set()
            for m in re.finditer(r"case\s+static_cast<int32_t>\(VehicleProperty::(\w+)\)\s*:\s*\{", cases_text):
                name = m.group(1)
                depth = 1
                pos = m.end()
                while depth > 0 and pos < len(cases_text):
                    if cases_text[pos] == "{":
                        depth += 1
                    elif cases_text[pos] == "}":
                        depth -= 1
                    pos += 1
                if name in kept_names:
                    continue
                kept_blocks.append(cases_text[m.start():pos])
                kept_names.add(name)
            return "\n".join(kept_blocks)

        merged_read_cases  = _dedup_across_chunks("\n".join(all_read_cases))
        merged_write_cases = _dedup_across_chunks("\n".join(all_write_cases))

        _READ_PLACEHOLDER  = "/*__READ_CASES_PLACEHOLDER__*/"
        _WRITE_PLACEHOLDER = "/*__WRITE_CASES_PLACEHOLDER__*/"
        if _READ_PLACEHOLDER in impl:
            impl = impl.replace(_READ_PLACEHOLDER, merged_read_cases, 1)
        else:
            impl += f"\n// WARNING: could not locate read-cases insertion point — appended raw:\n{merged_read_cases}\n"
        if _WRITE_PLACEHOLDER in impl:
            impl = impl.replace(_WRITE_PLACEHOLDER, merged_write_cases, 1)
        else:
            impl += f"\n// WARNING: could not locate write-cases insertion point — appended raw:\n{merged_write_cases}\n"

        class _PredLike:
            def __init__(self, **kw):
                for k, v in kw.items():
                    setattr(self, k, v)

        pred = _PredLike(
            domain=domain, cpp_header=header, cpp_impl=impl,
            main_service="", properties=aidl_block,
        )
        result = self.assertions(pred)

        return {
            "header":       result.cpp_header,
            "impl":         result.cpp_impl,
            "main_service": "",
            "android_bp":   "",
            "reasoning":    f"Chunked generation: {len(prop_list)} properties in "
                            f"{(len(prop_list) + chunk_size - 1) // chunk_size} chunk(s)",
            "violations":   result.violations,
            "chunk_retries": chunk_retry_count,
        }

    def _generate(self, domain: str, properties: str, aosp_context: str = "", **kwargs) -> str:
        """Alias for C4 retry engine — returns impl string, not dict."""
        out = self.generate(domain=domain, properties=properties,
                            extra_context=aosp_context)
        return out.get("impl", "")

    def repair(
        self,
        previous_output: dict,
        violations:      list,
        domain:          str,
        properties:      str,
    ) -> dict:
        summary = "\n".join(f"  [{i+1}] {v}" for i, v in enumerate(violations))
        extra   = (
            f"=== REPAIR — fix ALL violations before anything else ===\n"
            f"{summary}\n"
            f"Regenerate complete files. Do not repeat any violation.\n"
            f"=== END REPAIR ==="
        )
        return self.generate(domain, properties, extra_context=extra)


class RAGDSPyCppAgent:
    """Thin wrapper for RAGDSPyArchitectAgent and C4 retry engine compatibility.

    enable_chunk_retry controls whether chunk-level retry is active:
    - False (default): C3 behaviour — single shot per chunk, no retry.
      Retry is a C4 contribution; enabling it in C3 would blur the
      ablation boundary between the two conditions.
    - True: C4/C4-minimal behaviour — retry chunks that produce fewer
      entries than expected, up to MAX_CHUNK_RETRIES attempts.
    """

    def __init__(self, enable_chunk_retry: bool = False, **kwargs):
        self.inner = RagDspyCppAgent(**kwargs)
        self.enable_chunk_retry = enable_chunk_retry

    def run(self, module_spec, aidl_dir: str = "") -> dict:
        """Called by architect — returns dict with header + impl.

        Domains with more properties than CHUNK_SIZE use the chunked
        path (generate_chunked) to avoid max_tokens truncation of
        getAllPropertyConfigs() — see CHUNK_SIZE docstring above for
        why this exists (BODY=84 and POWERTRAIN=120 properties were
        observed truncating mid-token under the single-shot path).

        aidl_dir: path to the directory containing the real generated
        .aidl files for this run. When provided, property names are
        read from that real file instead of prop_list's own tracked
        names — see generate_chunked's aidl_dir docstring for why.
        """
        prop_list = getattr(module_spec, "properties", None) or []
        if len(prop_list) > CHUNK_SIZE:
            full_spec_text = module_spec.to_llm_spec()
            aidl_marker = "=== Generated AIDL enum"
            idx = full_spec_text.find(aidl_marker)
            aidl_block = full_spec_text[idx:] if idx != -1 else ""
            return self.inner.generate_chunked(
                domain             = module_spec.domain,
                prop_list          = prop_list,
                aidl_block         = aidl_block,
                enable_chunk_retry = self.enable_chunk_retry,
                aidl_dir           = aidl_dir,
            )

        out = self.inner.generate(
            domain     = module_spec.domain,
            properties = module_spec.to_llm_spec(),
        )
        return out  # dict with header, impl, main_service, android_bp

    def _generate(self, domain: str, properties: str,
                  aosp_context: str = "", prop_list: list = None,
                  aidl_dir: str = "", previous_code: str = "", **kwargs) -> str:
        """Called by C4 retry engine.

        BUG THIS FIXES: previously this always called self.inner._generate()
        — a single-shot regeneration of the ENTIRE domain in one LLM call —
        regardless of domain size. For large domains (BODY=84,
        POWERTRAIN=120, CABIN=168 properties), this meant retry attempts
        bypassed the chunked path entirely (the same path run() correctly
        uses for primary generation, specifically to avoid max_tokens
        truncation — see CHUNK_SIZE docstring above). A retry triggered
        by the CPP↔AIDL name-consistency gate would frequently produce a
        WORSE-scoring single-shot regeneration than the original chunked
        output, so `if score > best_score` never fired and the retry
        loop kept re-writing the original (still name-inconsistent)
        content on every attempt — observed empirically: BODY domain
        retry never converged in 3 attempts, with attempt 3 scoring
        LOWER (0.797) than the original (1.000 structurally, but
        name-inconsistent).

        Fix: when the caller provides `prop_list` (the domain's actual
        property list — retry callers now pass this) and it exceeds
        CHUNK_SIZE, route through the SAME generate_chunked() path
        run() uses, so retry has the same truncation-avoidance and
        chunk-level self-correction (register-body missing-case retry)
        as primary generation. Falls back to single-shot _generate()
        when prop_list isn't provided (backward compatible with any
        caller not yet updated) or is small enough not to need chunking.

        SECOND BUG THIS FIXES (previous_code param): even with the fix
        above, every retry still regenerated ALL chunks from scratch —
        observed directly in production (CABIN, 3 attempts): attempt 1
        failed on a duplicate case in one property, attempt 2 fixed that
        but introduced an UNRELATED error (wrong field name) in a
        DIFFERENT property, attempt 3 fixed THAT but reintroduced a
        duplicate case for a THIRD, different property. Full-domain
        regeneration is probabilistic per attempt, not a surgical patch —
        fixing one chunk's issue does not prevent a completely
        independent chunk from failing differently on the same retry.

        When `previous_code` (the PRIOR attempt's full generated code) is
        given, this identifies which chunk(s) the reported error(s)
        actually belong to and passes that to generate_chunked() so ONLY
        those chunks are regenerated — every other chunk's
        already-working code is reused verbatim from previous_code,
        rather than re-rolled and risking a brand new, unrelated failure.
        """
        if prop_list and len(prop_list) > CHUNK_SIZE:
            aidl_marker = "=== Generated AIDL enum"
            idx = properties.find(aidl_marker)
            aidl_block = properties[idx:] if idx != -1 else ""

            # Extract the validation-error feedback the retry engine
            # prepended to `properties` (see _retry_agent /
            # PostValidationRetry.validate_and_retry_file — both build
            # `properties` as "=== CRITICAL: FIX THESE VALIDATION
            # ERRORS FIRST ===\n<feedback>\n=== END ERRORS ===\n\n
            # === ORIGINAL PROPERTIES ===\n<original>"). Previously
            # this whole string was scanned ONLY for aidl_block and
            # then discarded — meaning a retry triggered by e.g. a
            # duplicate-case-value error regenerated the domain with
            # NO knowledge that anything needed fixing, from the same
            # prop_list as the original attempt. Passing it through as
            # extra_context wires it into the SAME mechanism
            # generate_chunked() already uses to inject context into
            # entries_predictor/register_body_predictor's prompts (see
            # `if extra_context: aosp_context = extra_context + ...`
            # below) — the retry now actually tells the LLM what broke
            # last time, instead of blindly resampling.
            error_marker = "=== CRITICAL: FIX THESE VALIDATION ERRORS FIRST ==="
            error_end_marker = "=== END ERRORS ==="
            error_feedback = ""
            if error_marker in properties:
                start = properties.find(error_marker)
                end = properties.find(error_end_marker, start)
                if end != -1:
                    error_feedback = properties[start:end + len(error_end_marker)]

            result = self.inner.generate_chunked(
                domain             = domain,
                prop_list          = prop_list,
                aidl_block         = aidl_block,
                extra_context      = error_feedback,
                enable_chunk_retry = self.enable_chunk_retry,
                aidl_dir           = aidl_dir,
                previous_full_code = previous_code,
            )
            return result.get("impl", "")
        return self.inner._generate(domain=domain, properties=properties,
                                    aosp_context=aosp_context)