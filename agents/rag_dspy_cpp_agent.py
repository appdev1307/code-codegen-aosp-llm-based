# agents/rag_dspy_cpp_agent.py
import re
import dspy
from rag.aosp_retriever import AOSPRetriever
from dspy_opt.hal_signatures import (
    ModernCppVehicleHardwareSignature,
    CppSkeletonSignature,
    CppPropertyEntriesSignature,
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
CHUNK_SIZE = 30

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

        all_entries = []
        for i in range(0, len(prop_list), chunk_size):
            chunk = prop_list[i:i + chunk_size]
            chunk_properties_text = self._build_chunk_properties_text(domain, chunk, aidl_block)
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
    """Thin wrapper for RAGDSPyArchitectAgent and C4 retry engine compatibility."""

    def __init__(self, **kwargs):
        self.inner = RagDspyCppAgent(**kwargs)

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
            # Extract just the AIDL enum block (if the caller already
            # embedded one via to_llm_spec(), e.g. gen_hal_minimal_c4.py's
            # _PatchedSpec) so each chunk call and the final
            # hallucination cross-check have real ground truth.
            full_spec_text = module_spec.to_llm_spec()
            aidl_marker = "=== Generated AIDL enum"
            idx = full_spec_text.find(aidl_marker)
            aidl_block = full_spec_text[idx:] if idx != -1 else ""
            return self.inner.generate_chunked(
                domain     = module_spec.domain,
                prop_list  = prop_list,
                aidl_block = aidl_block,
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