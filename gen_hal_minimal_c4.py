"""
gen_hal_minimal_c4.py
══════════════════════════════════════════════════════════════
Minimal HAL generation: AIDL + CPP + SELinux + VssGlue only.
No backend, no android_app, no design_doc — saves Colab time.
For testing VssGlueAgent fix and GCP VM integration.

Usage (Colab cell):
    %run gen_hal_minimal.py
    # or
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
from agents.rag_dspy_cpp_agent     import RagDspyCppAgent
from agents.rag_dspy_selinux_agent import RAGDSPySELinuxAgent
from agents.vss_glue_agent         import VssGlueAgent
from agents.module_planner_agent   import plan_modules_from_spec
from schemas.yaml_loader           import load_hal_spec_from_yaml_text

# ── Config ─────────────────────────────────────────────────
OUTPUT_DIR  = Path("output_c4_minimal")
YAML_SPEC   = Path("output_c4_feedback/SPEC_FROM_VSS_500.yaml")  # reuse existing spec
AGENT_CFG   = dict(dspy_programs_dir="dspy_opt/saved", rag_top_k=8, rag_db_path="rag/chroma_db")

# ── Output dirs ────────────────────────────────────────────
AIDL_OUT = OUTPUT_DIR / "hardware/interfaces/automotive/vehicle/aidl/android/hardware/automotive/vehicle"
CPP_OUT  = OUTPUT_DIR / "hardware/interfaces/automotive/vehicle/impl"
SE_OUT   = OUTPUT_DIR / "sepolicy"
VSS_OUT  = OUTPUT_DIR / "hardware/interfaces/automotive/vehicle/aidl/impl/vss"
for d in [AIDL_OUT, CPP_OUT, SE_OUT, VSS_OUT]:
    d.mkdir(parents=True, exist_ok=True)

print("══════════════════════════════════════════════════")
print("  Minimal HAL Generation: AIDL + CPP + SELinux")
print("══════════════════════════════════════════════════")

# ── Load spec ──────────────────────────────────────────────
yaml_spec         = YAML_SPEC.read_text()
full_spec         = load_hal_spec_from_yaml_text(yaml_spec)
module_signal_map = plan_modules_from_spec(yaml_spec, use_fast_mode=True)
properties_by_id  = {getattr(p,"id",None): p for p in full_spec.properties if getattr(p,"id",None)}
print(f"  Spec   : {len(full_spec.properties)} properties")
print(f"  Modules: {list(module_signal_map.keys())}")

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
            name   = getattr(prop, "id",     "UNKNOWN")
            typ    = getattr(prop, "type",   "UNKNOWN")
            access = getattr(prop, "access", "READ_WRITE")
            areas  = getattr(prop, "areas",  ["GLOBAL"])
            areas_str = ", ".join(areas) if isinstance(areas, (list, tuple)) else str(areas)
            lines += [f"- Name: {name}", f"  Type: {typ}",
                      f"  Access: {access}", f"  Areas: {areas_str}", ""]
        return "\n".join(lines)

# ── Init agents ────────────────────────────────────────────
aidl_agent    = RAGDSPyAIDLAgent(**AGENT_CFG)
cpp_agent     = RagDspyCppAgent(**AGENT_CFG)
selinux_agent = RAGDSPySELinuxAgent(**AGENT_CFG)

# ── Generate per module ────────────────────────────────────
scores = {"aidl": [], "cpp": [], "selinux": []}
t_total = time.time()

for domain, signal_ids in module_signal_map.items():
    print(f"\n{'='*52}")
    print(f"  MODULE: {domain} ({len(signal_ids)} signals)")
    print(f"{'='*52}")
    t0 = time.time()

    # Build ModuleSpec for this domain
    domain_props = [p for p in full_spec.properties
                    if getattr(p, "id", "") in signal_ids]
    mspec = ModuleSpec(domain=domain, properties=domain_props)

    # ── AIDL ──────────────────────────────────────────────
    try:
        code = aidl_agent.run(mspec)
        fpath = AIDL_OUT / f"VehicleProperty{domain.capitalize()}.aidl"
        fpath.write_text(code)
        s = score_file("aidl", code)
        r = validate("aidl", code)
        scores["aidl"].append(s)
        print(f"  [{'✓' if r.ok else '✗'}] aidl    score={s:.3f} ({len(code)} chars)")
    except Exception as e:
        print(f"  [✗] aidl    ERROR: {e}")

    # ── CPP ───────────────────────────────────────────────
    try:
        code = cpp_agent.run(mspec)
        # cpp_agent.run returns impl string
        fname = f"VehicleHalService{domain.capitalize()}.cpp"
        fpath = CPP_OUT / fname
        fpath.write_text(code if isinstance(code, str) else str(code))
        s = score_file("cpp", code if isinstance(code, str) else str(code))
        r = validate("cpp", code if isinstance(code, str) else str(code))
        scores["cpp"].append(s)
        print(f"  [{'✓' if r.ok else '✗'}] cpp     score={s:.3f} ({len(str(code))} chars)")
    except Exception as e:
        print(f"  [✗] cpp     ERROR: {e}")

    # ── SELinux ───────────────────────────────────────────
    try:
        code = selinux_agent.run(mspec)
        fpath = SE_OUT / f"vehicle_hal_{domain.lower()}.te"
        fpath.write_text(code)
        s = score_file("selinux", code)
        r = validate("selinux", code)
        scores["selinux"].append(s)
        print(f"  [{'✓' if r.ok else '✗'}] selinux score={s:.3f} ({len(code)} chars)")
    except Exception as e:
        print(f"  [✗] selinux ERROR: {e}")

    print(f"  Done in {time.time()-t0:.1f}s")

# ── VssGlueAgent (fixed prop IDs) ─────────────────────────
print(f"\n{'='*52}")
print("  VssGlueAgent (full 32-bit prop IDs)")
print(f"{'='*52}")
try:
    agent = VssGlueAgent()
    agent.run(str(VSS_OUT), aidl_dir=str(AIDL_OUT))
    # Verify prop IDs
    import re
    cpp_content = (VSS_OUT / "VssVehicleHardware.cpp").read_text()
    raw_ids = re.findall(r'cfg\.prop = (0x[0-9a-fA-F]+|\d+)', cpp_content)
    invalid = [x for x in raw_ids if int(x, 16) < 0x00100000]
    print(f"  Props  : {len(raw_ids)} total, {len(invalid)} invalid")
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
print(f"\n{'='*52}")
print("  SUMMARY")
print(f"{'='*52}")
for agent, sc in scores.items():
    avg = sum(sc)/len(sc) if sc else 0
    print(f"  {agent:<10}: {avg:.3f} ({len(sc)} files)")
print(f"  Total time: {time.time()-t_total:.1f}s")
print(f"  Output: {OUTPUT_DIR}")

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