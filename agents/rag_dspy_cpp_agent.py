# agents/rag_dspy_cpp_agent.py
import re
import dspy
from rag.aosp_retriever import AOSPRetriever
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
MAX_CHUNK_RETRIES = 2
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

    def generate_chunked(
        self,
        domain:        str,
        prop_list:     list,
        aidl_block:    str = "",
        extra_context: str = "",
        chunk_size:    int = CHUNK_SIZE,
        enable_chunk_retry: bool = False,
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
        for i in range(0, len(prop_list), chunk_size):
            chunk = prop_list[i:i + chunk_size]
            chunk_num = i // chunk_size + 1
            total_chunks = (len(prop_list) + chunk_size - 1) // chunk_size
            chunk_properties_text = self._build_chunk_properties_text(domain, chunk, aidl_block)

            chunk_entries = ""
            if enable_chunk_retry:
                for attempt in range(1, MAX_CHUNK_RETRIES + 2):
                    entries_result = self.entries_predictor(
                        domain       = domain,
                        properties   = chunk_properties_text,
                        aosp_context = aosp_context,
                    )
                    raw = _strip_markdown_fences(getattr(entries_result, "entries", "") or "")
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
                        print(f"  [CPP chunk {chunk_num}/{total_chunks}] "
                              f"✗ gave up after {MAX_CHUNK_RETRIES} retries: "
                              f"{generated_count}/{expected_count} entries")
                        chunk_entries = raw
            else:
                # C3 path: no retry — single shot per chunk, by design.
                entries_result = self.entries_predictor(
                    domain       = domain,
                    properties   = chunk_properties_text,
                    aosp_context = aosp_context,
                )
                chunk_entries = _strip_markdown_fences(getattr(entries_result, "entries", "") or "")

            all_entries.append(chunk_entries.strip())

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
                kept_blocks.append(cases_text[m.start():pos])
                kept_names.add(name)
            return "\n".join(kept_blocks), kept_names

        all_read_cases, all_write_cases = [], []
        for i in range(0, len(prop_list), chunk_size):
            chunk = prop_list[i:i + chunk_size]
            chunk_properties_text = self._build_chunk_properties_text(domain, chunk, aidl_block)
            chunk_names = {getattr(p, "id", None) or getattr(p, "signal_name", None) for p in chunk}
            chunk_rw_names = {
                getattr(p, "id", None) or getattr(p, "signal_name", None)
                for p in chunk
                if str(getattr(p, "access", "")).upper() in ("READ_WRITE", "WRITE")
            }

            read_cases_text, write_cases_text = "", ""
            if enable_chunk_retry:
                for attempt in range(1, MAX_CHUNK_RETRIES + 2):
                    body_result = self.register_body_predictor(
                        domain       = domain,
                        properties   = chunk_properties_text,
                        aosp_context = aosp_context,
                    )
                    raw_read  = _strip_markdown_fences(getattr(body_result, "read_cases", "") or "")
                    raw_write = _strip_markdown_fences(getattr(body_result, "write_cases", "") or "")
                    read_cases_text, kept_read   = _filter_cases_to_allowed_names(raw_read, chunk_names)
                    write_cases_text, kept_write = _filter_cases_to_allowed_names(raw_write, chunk_rw_names)

                    if kept_read >= chunk_names and kept_write >= chunk_rw_names:
                        if attempt > 1:
                            chunk_retry_count += 1
                            print(f"  [CPP register-body chunk {i // chunk_size + 1}] "
                                  f"✓ retry {attempt-1} recovered all cases")
                        break
                    if attempt <= MAX_CHUNK_RETRIES:
                        missing = sorted((chunk_names - kept_read) | (chunk_rw_names - kept_write))
                        missing_hint = "\n".join(f"  - {n}" for n in missing[:10])
                        retry_context = (
                            f"=== RETRY: MISSING cases for these properties ===\n"
                            f"{missing_hint}\n"
                            f"Output a case for EVERY property listed below — do not skip any.\n"
                            f"=== END RETRY ==="
                        )
                        chunk_properties_text = retry_context + "\n\n" + chunk_properties_text
                        print(f"  [CPP register-body chunk {i // chunk_size + 1}] "
                              f"⚠ attempt {attempt}: missing {len(missing)} case(s) — retrying...")
                    else:
                        print(f"  [CPP register-body chunk {i // chunk_size + 1}] "
                              f"✗ gave up after {MAX_CHUNK_RETRIES} retries: "
                              f"still missing {len((chunk_names - kept_read) | (chunk_rw_names - kept_write))}")
            else:
                # C3 path: no retry — single shot per chunk, by design.
                body_result = self.register_body_predictor(
                    domain       = domain,
                    properties   = chunk_properties_text,
                    aosp_context = aosp_context,
                )
                raw_read  = _strip_markdown_fences(getattr(body_result, "read_cases", "") or "")
                raw_write = _strip_markdown_fences(getattr(body_result, "write_cases", "") or "")
                read_cases_text, _  = _filter_cases_to_allowed_names(raw_read, chunk_names)
                write_cases_text, _ = _filter_cases_to_allowed_names(raw_write, chunk_rw_names)

            all_read_cases.append(read_cases_text.strip())
            all_write_cases.append(write_cases_text.strip())

        merged_read_cases  = "\n".join(all_read_cases)
        merged_write_cases = "\n".join(all_write_cases)

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

    def run(self, module_spec) -> dict:
        """Called by architect — returns dict with header + impl.

        Domains with more properties than CHUNK_SIZE use the chunked
        path (generate_chunked) to avoid max_tokens truncation of
        getAllPropertyConfigs() — see CHUNK_SIZE docstring above for
        why this exists (BODY=84 and POWERTRAIN=120 properties were
        observed truncating mid-token under the single-shot path).
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
            )

        out = self.inner.generate(
            domain     = module_spec.domain,
            properties = module_spec.to_llm_spec(),
        )
        return out  # dict with header, impl, main_service, android_bp

    def _generate(self, domain: str, properties: str,
                  aosp_context: str = "", **kwargs) -> str:
        """Called by C4 retry engine."""
        return self.inner._generate(domain=domain, properties=properties,
                                    aosp_context=aosp_context)