"""
multi_main_rag_dspy.py
═══════════════════════════════════════════════════════════════════════════════
VSS → AAOS HAL Generation Pipeline — Condition 3: RAG + DSPy

Differences from multi_main_adaptive.py (condition 2):
  1. All agents swapped for RAGDSPy variants
  2. Validator availability report printed at startup
  3. _generate_one_module() runs validators on every generated file
     and records structural / syntax / coverage scores separately
  4. experiment_summary() saves full per-file validator results to
     experiments/results/rag_dspy.json for thesis comparison

Prerequisites (run once before this script):
  Step 1 — Clone AOSP source repos (~2-3 GB):
    git clone https://android.googlesource.com/platform/hardware/interfaces aosp_source/hardware
    git clone https://android.googlesource.com/platform/system/sepolicy aosp_source/sepolicy
    git clone https://android.googlesource.com/platform/packages/services/Car aosp_source/car

  Step 2 — Build RAG vector index (~10 min):
    python -m rag.aosp_indexer --source aosp_source --db rag/chroma_db

  Step 3 — Optimise DSPy prompts (~30-90 min):
    python dspy_opt/optimizer.py

  Step 4 — Run this file:
    python multi_main_rag_dspy.py
═══════════════════════════════════════════════════════════════════════════════
"""

from __future__ import annotations

import json
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path

from vss_to_yaml import vss_to_yaml_spec
from schemas.yaml_loader import load_hal_spec_from_yaml_text
from agents.module_planner_agent import plan_modules_from_spec
from agents.promote_draft_agent import PromoteDraftAgent
from agents.build_glue_agent import BuildGlueAgent, ImprovedBuildGlueAgent
from agents.vss_labelling_agent import VSSLabellingAgent, flatten_vss
from tools.aosp_layout import ensure_aosp_layout

# ── Condition 3: RAG+DSPy agents ─────────────────────────────────────────────
from agents.rag_dspy_architect_agent   import RAGDSPyArchitectAgent
from agents.rag_dspy_selinux_agent     import RAGDSPySELinuxAgent
from agents.rag_dspy_design_doc_agent  import RAGDSPyDesignDocAgent
from agents.rag_dspy_android_app_agent import RAGDSPyAndroidAppAgent
from agents.rag_dspy_backend_agent     import RAGDSPyBackendAgent

# ── Compile-aware metrics + validators ───────────────────────────────────────
from dspy_opt.metrics    import score_file
from dspy_opt.validators import validate, print_availability_report

# Shared ChromaDB client singleton
# ChromaDB raises "instance already exists with different settings" when
# multiple PersistentClient objects open the same path concurrently.
# This singleton ensures every agent shares one connection.
_CHROMA_CLIENT = None

def get_chroma_client(db_path: str = "rag/chroma_db"):
    """Return the shared ChromaDB client, creating it on first call."""
    global _CHROMA_CLIENT
    if _CHROMA_CLIENT is None:
        import chromadb
        _CHROMA_CLIENT = chromadb.PersistentClient(path=str(db_path))
    return _CHROMA_CLIENT


# ─────────────────────────────────────────────────────────────────────────────
# Configuration
# ─────────────────────────────────────────────────────────────────────────────

TEST_SIGNAL_COUNT      = 50
VSS_PATH               = "./dataset/vss.json"
VENDOR_NAMESPACE       = "vendor.vss"
PERSISTENT_CACHE_DIR   = Path("/content/vss_temp")
PERSISTENT_CACHE_DIR.mkdir(parents=True, exist_ok=True)

OUTPUT_DIR             = Path("output_rag_dspy")   # isolated from conditions 1+2
RESULTS_DIR            = Path("experiments/results")
MAX_PARALLEL_LLM_CALLS = 4    # RAG+DSPy calls are heavier than baseline
BUILD_GLUE_LLM_TIMEOUT = 600

# Shared kwargs passed to every RAGDSPy agent constructor
AGENT_CFG = dict(
    dspy_programs_dir = "dspy_opt/saved",
    rag_top_k         = 3,
    rag_db_path       = "rag/chroma_db",
)

# Map agent_type → glob pattern to find generated files under OUTPUT_DIR
_FILE_PATTERNS: dict[str, str] = {
    "aidl":           "**/*.aidl",
    "cpp":            "**/*.cpp",
    "selinux":        "**/*.te",
    "build":          "**/Android.bp",
    "android_app":    "**/*Fragment*.kt",
    "android_layout": "**/fragment_*.xml",
    "backend":        "**/main.py",
    "backend_model":  "**/models_*.py",
    "simulator":      "**/simulator_*.py",
    "design_doc":     "**/DESIGN_DOCUMENT.md",
    "puml":           "**/*.puml",
}


# ─────────────────────────────────────────────────────────────────────────────
# ModuleSpec  (identical interface across all three conditions)
# ─────────────────────────────────────────────────────────────────────────────

class ModuleSpec:
    def __init__(self, domain: str, properties: list):
        self.domain     = domain.upper()
        self.properties = properties
        self.aosp_level = 14
        self.vendor     = "AOSP"

    def to_llm_spec(self) -> str:
        lines = [
            f"HAL Domain: {self.domain}",
            f"AOSP Level: {self.aosp_level}",
            f"Vendor: {self.vendor}",
            f"Properties: {len(self.properties)}",
            "",
        ]
        for prop in self.properties:
            name      = getattr(prop, "id",     "UNKNOWN")
            typ       = getattr(prop, "type",   "UNKNOWN")
            access    = getattr(prop, "access", "READ_WRITE")
            areas     = getattr(prop, "areas",  ["GLOBAL"])
            areas_str = ", ".join(areas) if isinstance(areas, (list, tuple)) else str(areas)
            lines += [f"- Name: {name}", f"  Type: {typ}",
                      f"  Access: {access}", f"  Areas: {areas_str}", ""]
        return "\n".join(lines)


# ─────────────────────────────────────────────────────────────────────────────
# Scoring helpers
# ─────────────────────────────────────────────────────────────────────────────

def _score_and_log(agent_type: str, fpath: Path) -> float:
    """
    Score one file using the three-tier metric and log the validator result.
    Returns the blended score (struct + syntax + coverage).
    """
    try:
        code   = fpath.read_text(encoding="utf-8", errors="ignore")
        score  = score_file(agent_type, code)
        result = validate(agent_type, code)
        status = "✓" if result.ok else "✗"
        errmsg = f"  ← {result.errors[0][:55]}" if result.errors else ""
        print(f"   [{status}] {agent_type:<18} "
              f"score={score:.3f}  "
              f"syntax={result.score:.3f} ({result.tool}){errmsg}")
        return score
    except Exception as e:
        print(f"   [?] {agent_type:<18} scoring failed: {e}")
        return 0.0


def _score_files(agent_types: list[str], domain_filter: str = "") -> dict[str, float]:
    """
    Walk OUTPUT_DIR, find files matching agent_type patterns, score them.
    If domain_filter is set, only match files whose path contains that string.
    Returns {agent_type: avg_score}.
    """
    scores: dict[str, list[float]] = {}
    for agent_type in agent_types:
        pattern = _FILE_PATTERNS.get(agent_type)
        if not pattern:
            continue
        suffix  = pattern.lstrip("**/")
        matches = list(OUTPUT_DIR.rglob(suffix))
        if domain_filter:
            matches = [f for f in matches
                       if domain_filter.lower() in f.name.lower()
                       or domain_filter.lower() in str(f.parent).lower()]
        for fpath in matches:
            s = _score_and_log(agent_type, fpath)
            scores.setdefault(agent_type, []).append(s)

    return {
        k: round(sum(v) / len(v), 4)
        for k, v in scores.items() if v
    }


def _avg(d: dict) -> float:
    vals = list(d.values())
    return round(sum(vals) / len(vals), 4) if vals else 0.0


# ─────────────────────────────────────────────────────────────────────────────
# Section 6 worker — per-module HAL generation
# ─────────────────────────────────────────────────────────────────────────────

def _generate_one_module(
    domain:       str,
    module_props: list,
    run_metrics:  list,
) -> tuple[str, bool, str | None]:
    """
    Generate all HAL layer files for one module via RAGDSPyArchitectAgent.
    Validates every generated file and appends score metrics to run_metrics.
    Returns (domain, success, error_msg).
    """
    print(f"\n{'=' * 60}")
    print(f" MODULE: {domain.upper()} ({len(module_props)} props)")
    print(f"{'=' * 60}")

    module_spec = ModuleSpec(domain=domain, properties=module_props)
    t0 = time.time()

    try:
        agent = RAGDSPyArchitectAgent(**AGENT_CFG)
        agent.run(module_spec)
    except Exception as e:
        print(f" [MODULE {domain}] → FAILED: {e}")
        run_metrics.append({
            "domain":          domain,
            "stage":           "hal_module",
            "success":         False,
            "error":           str(e),
            "generation_time": round(time.time() - t0, 2),
        })
        return (domain, False, str(e))

    elapsed = round(time.time() - t0, 2)

    # Score every generated HAL-layer file for this module
    print(f"\n   Validating {domain} output files:")
    file_scores = _score_files(
        ["aidl", "cpp", "selinux", "build"],
        domain_filter=domain,
    )
    avg_score = _avg(file_scores)

    run_metrics.append({
        "domain":          domain,
        "stage":           "hal_module",
        "success":         True,
        "generation_time": elapsed,
        "metric_score":    avg_score,
        "file_scores":     file_scores,
        "properties":      len(module_props),
    })

    print(f"\n [MODULE {domain}] → OK  "
          f"avg_score={avg_score:.3f}  ({elapsed:.1f}s)")
    return (domain, True, None)


# ─────────────────────────────────────────────────────────────────────────────
# Section 7 — support components (Group A parallel + Group B sequential)
# ─────────────────────────────────────────────────────────────────────────────

def _run_support_components(
    module_signal_map: dict,
    full_spec,
    yaml_spec:   str,
    run_metrics: list,
) -> None:

    def _run(stage: str, fn, agent_types: list[str]):
        t0 = time.time()
        try:
            fn()
            elapsed     = round(time.time() - t0, 2)
            file_scores = _score_files(agent_types)
            run_metrics.append({
                "stage":           stage,
                "success":         True,
                "generation_time": elapsed,
                "file_scores":     file_scores,
                "metric_score":    _avg(file_scores),
            })
        except Exception as e:
            run_metrics.append({
                "stage": stage, "success": False,
                "error": str(e),
                "generation_time": round(time.time() - t0, 2),
            })
            raise

    group_a = [
        ("design_doc",  lambda: RAGDSPyDesignDocAgent(**AGENT_CFG).run(
                            module_signal_map, full_spec.properties, yaml_spec),
                        ["design_doc", "puml"]),
        ("selinux",     lambda: RAGDSPySELinuxAgent(**AGENT_CFG).run(full_spec),
                        ["selinux"]),
        ("android_app", lambda: RAGDSPyAndroidAppAgent(**AGENT_CFG).run(
                            module_signal_map, full_spec.properties),
                        ["android_app", "android_layout"]),
        ("backend",     lambda: RAGDSPyBackendAgent(**AGENT_CFG).run(
                            module_signal_map, full_spec.properties),
                        ["backend", "backend_model", "simulator"]),
    ]

    with ThreadPoolExecutor(max_workers=len(group_a)) as pool:
        futures = {
            pool.submit(_run, stage, fn, atypes): stage
            for stage, fn, atypes in group_a
        }
        for future in as_completed(futures):
            stage = futures[future]
            try:
                future.result()
                print(f"  [SUPPORT] {stage} → OK")
            except Exception as e:
                print(f"  [SUPPORT] {stage} → FAILED: {e}")


# ─────────────────────────────────────────────────────────────────────────────
# Results
# ─────────────────────────────────────────────────────────────────────────────

def _save_results(run_metrics: list, t_total: float) -> dict:
    RESULTS_DIR.mkdir(parents=True, exist_ok=True)

    module_metrics = [m for m in run_metrics if m.get("stage") == "hal_module"]

    all_scores: list[float] = []
    per_agent:  dict[str, list[float]] = {}
    for m in run_metrics:
        for agent_type, score in m.get("file_scores", {}).items():
            per_agent.setdefault(agent_type, []).append(score)
            all_scores.append(score)
        if "metric_score" in m and not m.get("file_scores"):
            all_scores.append(m["metric_score"])

    summary = {
        "condition":             "rag_dspy",
        "total_signals":         TEST_SIGNAL_COUNT,
        "total_modules":         len(module_metrics),
        "modules_succeeded":     sum(1 for m in module_metrics if m.get("success")),
        "total_run_time_s":      round(t_total, 1),
        "avg_metric_score":      round(sum(all_scores) / len(all_scores), 4) if all_scores else 0.0,
        "avg_generation_time_s": round(
            sum(m.get("generation_time", 0) for m in run_metrics) / max(len(run_metrics), 1), 2
        ),
        "stages_succeeded":      sum(1 for m in run_metrics if m.get("success")),
        "stages_total":          len(run_metrics),
        "rag_top_k":             AGENT_CFG["rag_top_k"],
        "dspy_optimised":        (
            Path(AGENT_CFG["dspy_programs_dir"]) / "aidl_program" / "program.json"
        ).exists(),
        "per_agent_avg_scores":  {
            k: round(sum(v) / len(v), 4) for k, v in per_agent.items()
        },
        "per_stage_metrics":     run_metrics,
    }

    out_path = RESULTS_DIR / "rag_dspy.json"
    out_path.write_text(
        json.dumps(summary, indent=2, ensure_ascii=False), encoding="utf-8"
    )
    print(f"\n[RESULTS] Saved → {out_path}")
    return summary


def _print_summary(summary: dict) -> None:
    print("\n" + "=" * 65)
    print("  RAG+DSPy Pipeline — Run Summary")
    print("=" * 65)
    print(f"  Modules succeeded    : {summary['modules_succeeded']}/{summary['total_modules']}")
    print(f"  Avg metric score     : {summary['avg_metric_score']:.3f}")
    print(f"  Avg generation time  : {summary['avg_generation_time_s']:.1f}s")
    print(f"  Stages succeeded     : {summary['stages_succeeded']}/{summary['stages_total']}")
    print(f"  Total run time       : {summary['total_run_time_s']:.0f}s")
    print(f"  DSPy optimised       : {'yes' if summary['dspy_optimised'] else 'no (fallback)'}")
    print(f"  RAG top-k            : {summary['rag_top_k']}")

    if summary.get("per_agent_avg_scores"):
        print("\n  Per-agent avg scores (three-tier: struct+syntax+coverage):")
        for agent, score in sorted(summary["per_agent_avg_scores"].items()):
            bar    = "█" * int(score * 20)
            marker = "✓" if score >= 0.75 else ("~" if score >= 0.50 else "✗")
            print(f"    {marker} {agent:<18} {score:.3f}  {bar}")

    print("=" * 65)
    print(f"\n  Outputs  → {OUTPUT_DIR.resolve()}")
    print(f"  Results  → {RESULTS_DIR.resolve()}/rag_dspy.json")
    print()
    print("  Next steps:")
    print("    python experiments/run_comparison.py")
    print("    python experiments/analyze_results.py")
    print("=" * 65)


# ─────────────────────────────────────────────────────────────────────────────
# Preflight checks
# ─────────────────────────────────────────────────────────────────────────────

def _preflight_rag() -> bool:
    db_path = Path(AGENT_CFG["rag_db_path"])
    if not db_path.exists():
        print(f"[PREFLIGHT] ✗ ChromaDB not found: {db_path}")
        print("  Run: python -m rag.aosp_indexer --source aosp_source --db rag/chroma_db")
        return False
    try:
        # Use the singleton — this is the ONLY place ChromaDB is opened.
        # All agents will reuse this same client to avoid "different settings" error.
        client = get_chroma_client(str(db_path))
        cols   = client.list_collections()
        total  = sum(c.count() for c in cols)
        print(f"[PREFLIGHT] ✓ ChromaDB — {len(cols)} collections, {total:,} chunks")
        for c in cols:
            print(f"             {c.name}: {c.count()} chunks")
        return True
    except Exception as e:
        print(f"[PREFLIGHT] ✗ ChromaDB error: {e}")
        return False


def _preflight_dspy() -> None:
    saved_dir   = Path(AGENT_CFG["dspy_programs_dir"])
    agent_types = [
        "aidl","cpp","selinux","build","vintf",
        "design_doc","puml","android_app","android_layout",
        "backend","backend_model","simulator",
    ]
    found   = [a for a in agent_types
               if (saved_dir / f"{a}_program" / "program.json").exists()]
    missing = [a for a in agent_types if a not in found]

    print(f"[PREFLIGHT] DSPy programs: {len(found)}/{len(agent_types)} optimised")
    if missing:
        print(f"             Missing (unoptimised fallback): {', '.join(missing)}")
        print(f"             To optimise: python dspy_opt/optimizer.py "
              f"--agents {' '.join(missing)}")


# ─────────────────────────────────────────────────────────────────────────────
# main()
# ─────────────────────────────────────────────────────────────────────────────

def main():
    t_run_start = time.time()
    OUTPUT_DIR.mkdir(parents=True, exist_ok=True)

    print("=" * 70)
    print("  VSS → AAOS HAL Generation — Condition 3: RAG + DSPy")
    print("=" * 70)
    print(f"  Signals       : {TEST_SIGNAL_COUNT}")
    print(f"  Output dir    : {OUTPUT_DIR.resolve()}")
    print(f"  RAG db        : {AGENT_CFG['rag_db_path']}")
    print(f"  DSPy programs : {AGENT_CFG['dspy_programs_dir']}")
    print(f"  Max parallel  : {MAX_PARALLEL_LLM_CALLS}")
    print()

    # ── Print which validators are available (include in thesis methods) ───────
    print_availability_report()

    # ── Preflight ─────────────────────────────────────────────────────────────
    if not _preflight_rag():
        return
    _preflight_dspy()

    # Inject the already-open singleton into AGENT_CFG AND into the
    # rag.aosp_retriever module so get_retriever() reuses it.
    # This prevents "instance already exists with different settings" errors
    # when multiple agents try to open the same ChromaDB path in parallel.
    _client = get_chroma_client(AGENT_CFG["rag_db_path"])
    AGENT_CFG["chroma_client"] = _client
    try:
        import rag.aosp_retriever as _retriever_mod
        _retriever_mod._SHARED_CLIENT = _client   # patch the singleton slot
        print("[PREFLIGHT] ChromaDB singleton patched into rag.aosp_retriever ✓")
    except Exception as _e:
        print(f"[PREFLIGHT] Could not patch retriever module: {_e}")
    print()

    run_metrics: list[dict] = []

    # ── 1. Load + flatten VSS ─────────────────────────────────────────────────
    print(f"[PREP] Loading {VSS_PATH} ...")
    try:
        with open(VSS_PATH, "r", encoding="utf-8") as f:
            raw_vss = json.load(f)
        all_leaves = flatten_vss(raw_vss)
        print(f"       {len(all_leaves)} leaf signals found")
    except Exception as e:
        print(f"[ERROR] {e}")
        return

    selected_signals = dict(
        list(sorted(all_leaves.items()))[:TEST_SIGNAL_COUNT]
        if len(all_leaves) >= TEST_SIGNAL_COUNT
        else all_leaves.items()
    )

    limited_path = PERSISTENT_CACHE_DIR / f"VSS_LIMITED_{TEST_SIGNAL_COUNT}.json"
    limited_path.write_text(
        json.dumps(selected_signals, indent=2, ensure_ascii=False), encoding="utf-8"
    )

    # ── 2. Label ──────────────────────────────────────────────────────────────
    labelled_path = PERSISTENT_CACHE_DIR / f"VSS_LABELLED_{TEST_SIGNAL_COUNT}.json"
    if (labelled_path.exists()
            and labelled_path.stat().st_mtime >= limited_path.stat().st_mtime):
        print(f"[LABELLING] Cached labels: {labelled_path}")
        labelled_data = json.loads(labelled_path.read_text())
    else:
        print("[LABELLING] Labelling signals...")
        labelled_data = VSSLabellingAgent().run_on_dict(selected_signals)
        labelled_path.write_text(
            json.dumps(labelled_data, indent=2, ensure_ascii=False), encoding="utf-8"
        )
    print(f"             {len(labelled_data)} signals labelled")

    # ── 3. YAML spec ──────────────────────────────────────────────────────────
    print("\n[YAML] Converting to HAL YAML spec...")
    yaml_spec, prop_count = vss_to_yaml_spec(
        vss_json_path=str(labelled_path),
        include_prefixes=None, max_props=None,
        vendor_namespace=VENDOR_NAMESPACE, add_meta=True,
    )
    spec_path = OUTPUT_DIR / f"SPEC_FROM_VSS_{TEST_SIGNAL_COUNT}.yaml"
    spec_path.write_text(yaml_spec, encoding="utf-8")
    print(f"       {prop_count} properties")

    # ── 4. Load spec ──────────────────────────────────────────────────────────
    print("[LOAD] Loading HAL spec...")
    try:
        full_spec = load_hal_spec_from_yaml_text(yaml_spec)
    except Exception as e:
        print(f"[ERROR] {e}")
        return

    properties_by_id = {
        getattr(p, "id", None): p
        for p in full_spec.properties
        if getattr(p, "id", None)
    }
    print(f"       {len(properties_by_id)} unique property IDs")

    # ── 5. Module planning ────────────────────────────────────────────────────
    print("\n[PLAN] Running Module Planner...")
    try:
        module_signal_map = plan_modules_from_spec(yaml_spec)
        total = sum(len(v) for v in module_signal_map.values())
        print(f"       {len(module_signal_map)} modules, {total} signals")
    except Exception as e:
        print(f"[ERROR] {e}")
        return

    # ── 6. HAL module generation (parallel) ───────────────────────────────────
    print(f"\n[GEN] Generating {len(module_signal_map)} HAL modules "
          f"(max {MAX_PARALLEL_LLM_CALLS} parallel)...")

    tasks: list[tuple[str, list]] = []
    for domain, signal_names in module_signal_map.items():
        props = [properties_by_id[n] for n in signal_names if n in properties_by_id]
        if not props:
            print(f"  Skipping {domain} — no properties resolved")
            continue
        print(f"  {domain}: {len(props)}/{len(signal_names)} properties matched")
        tasks.append((domain, props))

    generated_count = 0
    with ThreadPoolExecutor(max_workers=MAX_PARALLEL_LLM_CALLS) as pool:
        futures = {
            pool.submit(_generate_one_module, domain, props, run_metrics): domain
            for domain, props in tasks
        }
        for future in as_completed(futures):
            _, ok, _ = future.result()
            if ok:
                generated_count += 1

    print(f"\n[GEN] HAL modules: {generated_count}/{len(tasks)} OK")

    # ── 7. Support components ─────────────────────────────────────────────────
    print("\n[SUPPORT] Generating design docs, SELinux, Android app, backend...")
    _run_support_components(module_signal_map, full_spec, yaml_spec, run_metrics)

    # Group B — PromoteDraft → BuildGlue (sequential, order matters)
    print("  [SUPPORT] Running PromoteDraft → BuildGlue...")
    t0 = time.time()
    try:
        PromoteDraftAgent().run()
        print("  [SUPPORT] PromoteDraft → OK")
    except Exception as e:
        print(f"  [SUPPORT] PromoteDraft → FAILED: {e}")

    try:
        module_plan_path = OUTPUT_DIR / "MODULE_PLAN.json"
        llm_client = None
        try:
            from llm_client import call_llm
            class _W:
                def generate(self, prompt, timeout=300):
                    try:    return call_llm(prompt, timeout=timeout)
                    except TypeError: return call_llm(prompt)
            llm_client = _W()
        except Exception:
            pass

        build_agent = (
            ImprovedBuildGlueAgent(
                output_root=str(OUTPUT_DIR),
                module_plan=str(module_plan_path) if module_plan_path.exists() else None,
                hal_spec=str(spec_path) if spec_path.exists() else None,
                llm_client=llm_client,
                timeout=BUILD_GLUE_LLM_TIMEOUT,
            ) if llm_client else
            BuildGlueAgent(
                output_root=str(OUTPUT_DIR),
                module_plan=str(module_plan_path) if module_plan_path.exists() else None,
                hal_spec=str(spec_path) if spec_path.exists() else None,
            )
        )

        ok            = build_agent.run()
        elapsed_build = round(time.time() - t0, 2)
        build_scores  = _score_files(["build"])
        run_metrics.append({
            "stage": "build_glue", "success": ok,
            "generation_time": elapsed_build,
            "file_scores":     build_scores,
            "metric_score":    _avg(build_scores),
        })
        print(f"  [SUPPORT] BuildGlue → {'OK' if ok else 'FAILED'}")

    except Exception as e:
        run_metrics.append({"stage": "build_glue", "success": False, "error": str(e)})
        print(f"  [SUPPORT] BuildGlue → FAILED: {e}")

    # ── 8. Save + print ───────────────────────────────────────────────────────
    t_total = round(time.time() - t_run_start, 1)
    summary = _save_results(run_metrics, t_total)
    _print_summary(summary)


if __name__ == "__main__":
    main()