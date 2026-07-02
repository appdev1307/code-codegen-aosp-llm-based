"""
gen_hal_minimal.py
══════════════════════════════════════════════════════════════
Minimal HAL generation: AIDL + CPP + SELinux + VssGlue only.
RAG + DSPy (C3) + post-validation retry (C4).
No backend, android_app, design_doc — saves Colab time.
For testing VssGlueAgent fix and GCP VM integration.

Usage (Colab cell):
    exec(open('gen_hal_minimal.py').read())
══════════════════════════════════════════════════════════════
"""

import importlib, json, sys, time, zipfile
from pathlib import Path
sys.path.insert(0, '.')

# ── Reload validators ──────────────────────────────────────
import dspy_opt.validators
importlib.reload(dspy_opt.validators)
from dspy_opt.validators import validate
from dspy_opt.metrics    import score_file

# ── ChromaDB singleton ─────────────────────────────────────
from apply_chroma_fix import *

# ── Agents ─────────────────────────────────────────────────
from agents.rag_dspy_aidl_agent    import RAGDSPyAIDLAgent
from agents.rag_dspy_cpp_agent     import RAGDSPyCppAgent
from agents.rag_dspy_selinux_agent import RAGDSPySELinuxAgent
from agents.vss_glue_agent         import VssGlueAgent
from agents.module_planner_agent   import plan_modules_from_spec
from schemas.yaml_loader           import load_hal_spec_from_yaml_text

# ── Config ─────────────────────────────────────────────────
OUTPUT_DIR       = Path("output_c4_minimal")

# Delete output dir first (clean run)
import shutil
if OUTPUT_DIR.exists():
    shutil.rmtree(OUTPUT_DIR)
    print(f"🗑  Deleted {OUTPUT_DIR}")
AGENT_CFG        = dict(dspy_programs_dir="dspy_opt/saved", rag_top_k=8, rag_db_path="rag/chroma_db")
MAX_RETRIES      = 3
LABELLED_CACHE   = Path("/content/vss_temp/VSS_LABELLED_500.json")
VENDOR_NAMESPACE = "vendor.vss"
TEST_SIGNAL_COUNT = 500

# ── Output dirs ────────────────────────────────────────────
AIDL_OUT = OUTPUT_DIR / "hardware/interfaces/automotive/vehicle/aidl/android/hardware/automotive/vehicle"
CPP_OUT  = OUTPUT_DIR / "hardware/interfaces/automotive/vehicle/impl"
SE_OUT   = OUTPUT_DIR / "sepolicy"
VSS_OUT  = OUTPUT_DIR / "hardware/interfaces/automotive/vehicle/aidl/impl/vss"
for d in [AIDL_OUT, CPP_OUT, SE_OUT, VSS_OUT]:
    d.mkdir(parents=True, exist_ok=True)

# ── ModuleSpec (same as C4) ────────────────────────────────
class ModuleSpec:
    def __init__(self, domain: str, properties: list):
        self.domain     = domain.upper()
        self.properties = properties
        self.aosp_level = 14
        self.vendor     = "AOSP"

    def to_llm_spec(self) -> str:
        lines = [f"HAL Domain: {self.domain}", f"AOSP Level: {self.aosp_level}",
                 f"Vendor: {self.vendor}", f"Properties: {len(self.properties)}", ""]
        for prop in self.properties:
            name      = getattr(prop, "id",     "UNKNOWN")
            typ       = getattr(prop, "type",   "UNKNOWN")
            access    = getattr(prop, "access", "READ_WRITE")
            areas     = getattr(prop, "areas",  ["GLOBAL"])
            areas_str = ", ".join(areas) if isinstance(areas, (list, tuple)) else str(areas)
            lines += [f"- Name: {name}", f"  Type: {typ}",
                      f"  Access: {access}", f"  Areas: {areas_str}", ""]
        return "\n".join(lines)

# ── Retry helper (same logic as C4 PostValidationRetry) ───
def _retry_agent(agent, agent_type, fpath, gen_kwargs, extra_files=None):
    """
    Validate generated file; retry with error feedback if it fails.
    Mirrors C4 PostValidationRetry.validate_and_retry_file().
    """
    if not fpath.exists():
        return False, 0.0, 0

    code = fpath.read_text(encoding="utf-8", errors="ignore")
    code_to_val = code
    if extra_files:
        extra = "\n".join(p.read_text(errors="ignore") for p in extra_files if p.exists())
        code_to_val = code + "\n" + extra

    r = validate(agent_type, code_to_val)
    if r.ok:
        return True, r.score, 1

    best_code, best_score = code, r.score
    error_msg = r.errors[0] if r.errors else "validation failed"
    print(f"    ✗ Initial failed (score={r.score:.3f}) — retrying...")

    for attempt in range(2, MAX_RETRIES + 1):
        feedback = (
            f"Your previous output had validation errors:\n{error_msg}\n\n"
            f"Fix ALL errors above. Generate the COMPLETE corrected file."
        )
        retry_kwargs = dict(gen_kwargs)
        # Inject error feedback into `properties` field (same as C4)
        retry_kwargs["properties"] = (
            "=== CRITICAL: FIX THESE VALIDATION ERRORS FIRST ===\n"
            + feedback
            + "\n=== END ERRORS ===\n\n"
            + "=== ORIGINAL PROPERTIES ===\n"
            + gen_kwargs.get("properties", "")
        )
        retry_kwargs["aosp_context"] = gen_kwargs.get("aosp_context", "")

        try:
            new_code = agent._generate(**retry_kwargs)
        except Exception as e:
            print(f"    Attempt {attempt}: generation error: {e}")
            continue

        if not new_code or not new_code.strip():
            print(f"    Attempt {attempt}: empty output")
            continue

        code_for_val = new_code
        if extra_files:
            extra = "\n".join(p.read_text(errors="ignore") for p in extra_files if p.exists())
            code_for_val = new_code + "\n" + extra

        r = validate(agent_type, code_for_val)
        if r.score > best_score:
            best_code, best_score = new_code, r.score

        if r.ok:
            fpath.write_text(new_code, encoding="utf-8")
            print(f"    ✓ Passed on attempt {attempt} (score={r.score:.3f})")
            return True, r.score, attempt

        error_msg = r.errors[0] if r.errors else "validation failed"
        print(f"    Attempt {attempt}: still failing (score={r.score:.3f})")

    # Write best version even if not passing
    fpath.write_text(best_code, encoding="utf-8")
    return False, best_score, MAX_RETRIES

# ── Init agents ────────────────────────────────────────────
print("══════════════════════════════════════════════════════")
print("  Minimal HAL: AIDL + CPP + SELinux (RAG+DSPy+Retry)")
print("══════════════════════════════════════════════════════")

aidl_agent    = RAGDSPyAIDLAgent(**AGENT_CFG)
cpp_agent     = RAGDSPyCppAgent(**AGENT_CFG)
selinux_agent = RAGDSPySELinuxAgent(**AGENT_CFG)

# ── Load spec (same as C4 — from labeled signals cache) ───
from vss_to_yaml import vss_to_yaml_spec

if LABELLED_CACHE.exists():
    print(f"[LABELLING] Cached labels: {LABELLED_CACHE}")
    yaml_spec, prop_count = vss_to_yaml_spec(
        vss_json_path=str(LABELLED_CACHE),
        include_prefixes=None, max_props=None,
        vendor_namespace=VENDOR_NAMESPACE, add_meta=True,
    )
    spec_path = OUTPUT_DIR / f"SPEC_FROM_VSS_{TEST_SIGNAL_COUNT}.yaml"
    spec_path.parent.mkdir(parents=True, exist_ok=True)
    spec_path.write_text(yaml_spec, encoding="utf-8")
    print(f"  {prop_count} properties from labelled signals")
else:
    raise FileNotFoundError(
        f"Labelled cache not found: {LABELLED_CACHE}\n"
        "Run VSSLabellingAgent first or restore from Google Drive."
    )

full_spec         = load_hal_spec_from_yaml_text(yaml_spec)
module_signal_map = plan_modules_from_spec(yaml_spec, use_fast_mode=True)
print(f"  Spec   : {len(full_spec.properties)} properties")
print(f"  Modules: {list(module_signal_map.keys())}")

# ── Generate per module ────────────────────────────────────
scores = {"aidl": [], "cpp": [], "selinux": []}
t_total = time.time()

def _get_aidl_content(domain: str) -> str:
    """Read generated AIDL enum to inject exact prop IDs into CPP prompt.
    Rewrites the per-domain enum name (e.g. VehiclePropertyAdas) to
    VehicleProperty so the LLM generates VehicleProperty::PROP_NAME —
    the correct prefix after all domains are merged into the single
    aidl_property/VehicleProperty.aidl on the build system.
    """
    import glob as _glob, re as _re
    files = _glob.glob(str(AIDL_OUT / f"VehicleProperty{domain.capitalize()}.aidl"))
    if not files:
        return ""
    raw = open(files[0], errors="ignore").read()
    # Rewrite "enum VehiclePropertyAdas {" → "enum VehicleProperty {"
    raw = _re.sub(r"\benum\s+VehicleProperty\w+\s*\{", "enum VehicleProperty {", raw)
    return (
        "\n=== Generated AIDL enum (use these exact prop IDs) ===\n"
        "// NOTE: All VSS properties are merged into VehicleProperty in VehicleProperty.h\n"
        "// Use VehicleProperty::PROP_NAME — NOT VehiclePropertyAdas:: or other per-domain prefixes\n"
        + raw
    )

for domain, signal_ids in module_signal_map.items():
    print(f"\n{'='*54}")
    print(f"  MODULE: {domain} ({len(signal_ids)} signals)")
    print(f"{'='*54}")
    t0 = time.time()

    domain_props = [p for p in full_spec.properties
                    if getattr(p, "id", "") in signal_ids]
    mspec    = ModuleSpec(domain=domain, properties=domain_props)
    llm_spec = mspec.to_llm_spec()

    # RAG context per agent type
    def _rag(agent, query):
        return agent._retrieve(query) if hasattr(agent, '_retrieve') else ""

    # ── AIDL ──────────────────────────────────────────────
    try:
        aidl_code = aidl_agent.run(mspec)
        fpath = AIDL_OUT / f"VehicleProperty{domain.capitalize()}.aidl"
        fpath.write_text(aidl_code)
        rag_ctx = _rag(aidl_agent, f"VehicleProperty enum AIDL {domain} android automotive")
        passed, score, attempts = _retry_agent(
            agent=aidl_agent, agent_type="aidl", fpath=fpath,
            gen_kwargs={"domain": domain, "properties": llm_spec, "aosp_context": rag_ctx}
        )
        scores["aidl"].append(score)
        print(f"  [{'✓' if passed else '~'}] aidl    score={score:.3f}  attempts={attempts}")
    except Exception as e:
        print(f"  [✗] aidl    ERROR: {e}")

    # ── CPP ───────────────────────────────────────────────
    try:
        # Inject AIDL content into mspec for CPP agent
        aidl_content = _get_aidl_content(domain)
        if aidl_content:
            # Patch module_spec to include AIDL enum in llm_spec
            class _PatchedSpec:
                def __init__(self, spec, extra):
                    self._spec = spec
                    self._extra = extra
                    self.domain = spec.domain
                    self.properties = spec.properties
                def to_llm_spec(self):
                    return self._spec.to_llm_spec() + self._extra
            patched_spec = _PatchedSpec(mspec, aidl_content)
        else:
            patched_spec = mspec
        cpp_result = cpp_agent.run(patched_spec)
        domain_cap = domain.capitalize()
        impl_fpath = CPP_OUT / f"VehicleHalService{domain_cap}.cpp"
        # Extract header and impl from dict result
        if isinstance(cpp_result, dict):
            if cpp_result.get("header"):
                (CPP_OUT / f"VehicleHalService{domain_cap}.h").write_text(cpp_result["header"])
            impl_fpath.write_text(cpp_result.get("impl", ""))
        else:
            impl_fpath.write_text(str(cpp_result))
        extra = [CPP_OUT / f"VehicleHalService{domain_cap}.h",
                 CPP_OUT / f"VehicleService{domain_cap}.cpp"]
        rag_ctx = _rag(cpp_agent, f"IVehicleHardware CPP {domain} android automotive vehicle")
        passed, score, attempts = _retry_agent(
            agent=cpp_agent, agent_type="cpp", fpath=impl_fpath,
            gen_kwargs={"domain": domain, "properties": llm_spec + _get_aidl_content(domain), "aosp_context": rag_ctx},
            extra_files=extra
        )
        scores["cpp"].append(score)
        print(f"  [{'✓' if passed else '~'}] cpp     score={score:.3f}  attempts={attempts}")
    except Exception as e:
        print(f"  [✗] cpp     ERROR: {e}")

    # ── SELinux ───────────────────────────────────────────
    try:
        se_code = selinux_agent.run(mspec)
        fpath = SE_OUT / f"vehicle_hal_{domain.lower()}.te"
        fpath.write_text(se_code)
        rag_ctx = _rag(selinux_agent, f"hal_vehicle SELinux AIDL Android 14 binder {domain}")
        passed, score, attempts = _retry_agent(
            agent=selinux_agent, agent_type="selinux", fpath=fpath,
            gen_kwargs={"domain": domain, "service_name": f"vendor.vss.{domain.lower()}",
                        "aosp_context": rag_ctx}
        )
        scores["selinux"].append(score)
        print(f"  [{'✓' if passed else '~'}] selinux score={score:.3f}  attempts={attempts}")
    except Exception as e:
        print(f"  [✗] selinux ERROR: {e}")

    print(f"  Done in {time.time()-t0:.1f}s")

# ── VssGlueAgent (fixed 32-bit prop IDs) ──────────────────
print(f"\n{'='*54}")
print("  VssGlueAgent (full 32-bit prop IDs)")
print(f"{'='*54}")
try:
    agent = VssGlueAgent()
    agent.run(str(VSS_OUT), aidl_dir=str(AIDL_OUT))
    import re
    cpp_content = (VSS_OUT / "VssVehicleHardware.cpp").read_text()
    raw_ids = re.findall(r'mPropIds\.push_back\((0x[0-9a-fA-F]+)\)', cpp_content)
    invalid = [x for x in raw_ids if int(x, 16) < 0x20000000]
    print(f"  Props  : {len(raw_ids)} total")
    print(f"  Sample : {raw_ids[:3]}")
    print(f"  IDs    : {'✅ All valid 32-bit VHAL IDs' if not invalid else '❌ ' + str(invalid[:3])}")
except Exception as e:
    print(f"  ❌ VssGlueAgent ERROR: {e}")

# ── AIDL interface Android.bp ─────────────────────────────
print(f"\n[+] Generating AIDL interface Android.bp...")
aidl_bp = """\
package {
    default_applicable_licenses: ["hardware_interfaces_license"],
}

aidl_interface {
    name: "android.hardware.automotive.vehicle",
    vendor_available: true,
    srcs: ["android/hardware/automotive/vehicle/*.aidl"],
    stability: "vintf",
    frozen: false,
    backend: {
        java: { enabled: false },
        cpp: { enabled: false },
        ndk: {
            enabled: true,
            apex_available: [
                "//apex_available:platform",
                "com.android.car.framework",
            ],
        },
    },
    versions_with_info: [
        { version: "1", imports: [] },
        { version: "2", imports: [] },
    ],
}
"""
bp_path = OUTPUT_DIR / "hardware/interfaces/automotive/vehicle/aidl/Android.bp"
bp_path.parent.mkdir(parents=True, exist_ok=True)
bp_path.write_text(aidl_bp)
print(f"  ✅ AIDL Android.bp generated")

# ── Summary ────────────────────────────────────────────────
print(f"\n{'='*54}")
print("  SUMMARY")
print(f"{'='*54}")
for agent_type, sc in scores.items():
    avg = sum(sc)/len(sc) if sc else 0.0
    print(f"  {agent_type:<10}: avg={avg:.3f} ({len(sc)} files)")
print(f"  Total time : {time.time()-t_total:.1f}s")
print(f"  Output dir : {OUTPUT_DIR}")

# ── Zip for GCP VM ────────────────────────────────────────
print(f"\n[+] Zipping for GCP VM...")
zip_path = Path("output_c4_minimal.zip")
with zipfile.ZipFile(zip_path, 'w', zipfile.ZIP_DEFLATED) as zf:
    for f in OUTPUT_DIR.rglob('*'):
        if f.is_file():
            zf.write(f, f.relative_to(OUTPUT_DIR))
print(f"  ✅ {zip_path} ({zip_path.stat().st_size/1024/1024:.1f} MB)")

try:
    from google.colab import files
    files.download(str(zip_path))
    print("  ✅ Download started")
except:
    print(f"  (Not in Colab — file at {zip_path})")

print("\nDone! Run on GCP VM:")
print("  unzip output_c4_minimal.zip -d ~/output_c4_minimal")
print("  ./apply_aosp14_fixes_fixed.sh ~/output_c4_minimal ~/aosp")
print("  m -j$(nproc)")

# ── Verify output ──────────────────────────────────────────
import os, glob

def verify_output(base=str(OUTPUT_DIR)):
    HIDL_BAD = [
        "hal_attribute_hwservice", "add_hwservice", "hwbinder_device",
        "IOnPropertyChangeCallback", "IOnPropertySetErrorCallback",
        "callback->onValues(", "callback->onResult(",
        "VssVehicleHardwareImpl",
        "<aidl/android/hardware/automotive/vehicle/DefaultVehicleHal.h>",
    ]
    A14_REQUIRED_IN_H = [
        "<IVehicleHardware.h>",
        "getAllPropertyConfigs",
        "GetValuesCallback",
    ]
    AIDL_REQUIRED = [
        "package android.hardware.automotive.vehicle",
        "@VintfStability",
        '@Backing(type="int")',
    ]

    print("=== 1. HIDL Contamination ===")
    hidl_issues = []
    for fpath in glob.glob(base + "/**/*", recursive=True):
        if not os.path.isfile(fpath): continue
        if not fpath.endswith((".cpp",".h",".te",".xml",".aidl")): continue
        content = open(fpath, errors="ignore").read()
        bad = [p for p in HIDL_BAD if p in content]
        if bad:
            hidl_issues.append((fpath.replace(base+"/",""), bad[:1]))
    if hidl_issues:
        print(f"✗ {len(hidl_issues)} files:")
        for f, b in hidl_issues[:5]: print(f"  {f}: {b}")
    else:
        print("✓ No HIDL patterns")

    print("\n=== 2. Android 14 Standard (VehicleHalService*.cpp/.h) ===")
    for cpp_path in sorted(glob.glob(base + "/**/VehicleHalService*.cpp", recursive=True)):
        cpp_txt = open(cpp_path, errors="ignore").read()
        h_path = cpp_path.replace(".cpp", ".h")
        h_txt = open(h_path, errors="ignore").read() if os.path.exists(h_path) else ""
        combined = cpp_txt + h_txt
        A14_REQUIRED = ["getAllPropertyConfigs", "getValues"]
        missing = [p for p in A14_REQUIRED if p not in combined]
        has_h = os.path.exists(h_path)
        fname = cpp_path.split("/")[-1]
        status = "✓" if not missing else "✗"
        h_status = "(.h ✓)" if has_h else "(.h ✗)"
        print(f"  {status} {fname} {h_status}" + (f": missing {missing}" if missing else ""))

    print("\n=== 3. AIDL Files ===")
    for fpath in sorted(glob.glob(base + "/**/VehicleProperty*.aidl", recursive=True)):
        content = open(fpath, errors="ignore").read()
        missing = [p for p in AIDL_REQUIRED if p not in content]
        lines = len(content.splitlines())
        fname = fpath.split("/")[-1]
        print(f"  {'✓' if not missing else '✗'} {fname} ({lines} lines)" + (f": missing {missing}" if missing else ""))

    print("\n=== 4. VssGlueAgent Artifacts ===")
    vss_dir = base + "/hardware/interfaces/automotive/vehicle/aidl/impl/vss"
    for f in ["VssVehicleHardware.h", "VssVehicleHardware.cpp",
              "Android.bp",
              "android.hardware.automotive.vehicle@V3-vss-service.rc",
              "manifest_vss.xml"]:
        exists = os.path.exists(os.path.join(vss_dir, f))
        print(f"  {'✓' if exists else '✗'} {f}")
    # VssGlueAgent may name service main differently
    service_main = next(
        (f for f in ["VehicleServiceMain.cpp", "VssVehicleService.cpp"]
         if os.path.exists(os.path.join(vss_dir, f))), None)
    print(f"  {'✓' if service_main else '✗'} {service_main or 'VehicleServiceMain.cpp (missing)'}")

    print("\n=== 5. SELinux Files ===")
    for fpath in sorted(glob.glob(base + "/**/vehicle_hal_*.te", recursive=True)):
        content = open(fpath, errors="ignore").read()
        ok = "type " in content and "init_daemon_domain" in content and "hal_server_domain" in content
        fname = fpath.split("/")[-1]
        print(f"  {'✓' if ok else '✗'} {fname}")

verify_output()