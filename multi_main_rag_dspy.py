"""
multi_main_rag_dspy.py
══════════════════════════════════════════════════════════════════════
VSS → AAOS HAL Generation Pipeline  —  Condition 3: RAG + DSPy

Thesis experiment entry point. Runs the same pipeline as
multi_main_adaptive.py (condition 2) but replaces every generation
agent with its RAG+DSPy variant:

  Condition 1 → multi_main.py             (plain LLM, hand-crafted prompts)
  Condition 2 → multi_main_adaptive.py    (adaptive chunking + prompt selection)
  Condition 3 → multi_main_rag_dspy.py    (RAG retrieval + DSPy-optimised prompts)

All three run on the IDENTICAL 50 VSS signals. Metric comparison
across the three conditions is the core thesis result.

Prerequisites (run once before this script):
  1. Clone AOSP source repos:
       git clone https://android.googlesource.com/platform/hardware/interfaces aosp_source/hardware
       git clone https://android.googlesource.com/platform/system/sepolicy       aosp_source/sepolicy
       git clone https://android.googlesource.com/platform/packages/services/Car aosp_source/car

  2. Build RAG vector index:
       python -m rag.aosp_indexer --source aosp_source --db rag/chroma_db

  3. Run DSPy optimiser (uses condition 2 baseline output as training data):
       python dspy_opt/optimizer.py

Sections that differ from multi_main_adaptive.py are marked:
  ← RAG+DSPy CHANGE
Everything else is identical to multi_main_adaptive.py.
══════════════════════════════════════════════════════════════════════
"""

from pathlib import Path
from concurrent.futures import ThreadPoolExecutor, as_completed
import json
import time
from vss_to_yaml import vss_to_yaml_spec
from schemas.yaml_loader import load_hal_spec_from_yaml_text
from agents.module_planner_agent import plan_modules_from_spec
from agents.promote_draft_agent import PromoteDraftAgent
from agents.build_glue_agent import BuildGlueAgent, ImprovedBuildGlueAgent
from agents.vss_labelling_agent import VSSLabellingAgent, flatten_vss
from tools.aosp_layout import ensure_aosp_layout

# ════════════════════════════════════════════════════════════════════
# ADAPTIVE IMPORTS  (same as condition 2)
# ════════════════════════════════════════════════════════════════════
from adaptive_integration import get_adaptive_wrapper

# ════════════════════════════════════════════════════════════════════
# RAG + DSPy IMPORTS  ← RAG+DSPy CHANGE
# Replaces:
#   ArchitectAgent                → RAGDSPyArchitectAgent
#   DesignDocAgentAdaptive        → RAGDSPyDesignDocAgent
#   generate_selinux              → RAGDSPySELinuxAgent
#   LLMAndroidAppAgentAdaptive    → RAGDSPyAndroidAppAgent
#   LLMBackendAgentAdaptive       → RAGDSPyBackendAgent
# ════════════════════════════════════════════════════════════════════
from agents.rag_dspy_architect_agent   import RAGDSPyArchitectAgent
from agents.rag_dspy_selinux_agent     import RAGDSPySELinuxAgent
from agents.rag_dspy_design_doc_agent  import RAGDSPyDesignDocAgent
from agents.rag_dspy_android_app_agent import RAGDSPyAndroidAppAgent
from agents.rag_dspy_backend_agent     import RAGDSPyBackendAgent
from rag.aosp_retriever                import get_retriever
from dspy_opt.metrics                  import METRIC_REGISTRY

# ────────────────────────────────────────────────
# Configurable parameters  (identical to other conditions)
# ────────────────────────────────────────────────
TEST_SIGNAL_COUNT   = 50
VSS_PATH            = "./dataset/vss.json"
VENDOR_NAMESPACE    = "vendor.vss"

PERSISTENT_CACHE_DIR = Path("/content/vss_temp")
PERSISTENT_CACHE_DIR.mkdir(parents=True, exist_ok=True)

OUTPUT_DIR = Path("output_rag_dspy")   # ← RAG+DSPy CHANGE: separate output dir
                                        #   keeps condition 3 results isolated
                                        #   from condition 1/2 outputs

MAX_PARALLEL_LLM_CALLS = 6
BUILD_GLUE_LLM_TIMEOUT = 600

# ════════════════════════════════════════════════════════════════════
# RAG + DSPy CONFIGURATION  ← RAG+DSPy CHANGE
# ════════════════════════════════════════════════════════════════════
RAG_DB_PATH           = "rag/chroma_db"
DSPY_PROGRAMS_DIR     = "dspy_opt/saved"
RAG_TOP_K             = 3      # retrieved chunks per agent call
METRICS_OUTPUT_PATH   = "experiments/results/rag_dspy.json"

# ────────────────────────────────────────────────
# ModuleSpec  (identical to other conditions)
# ────────────────────────────────────────────────
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
            ""
        ]
        for prop in self.properties:
            name     = getattr(prop, "id",     "UNKNOWN")
            typ      = getattr(prop, "type",   "UNKNOWN")
            access   = getattr(prop, "access", "READ_WRITE")
            areas    = getattr(prop, "areas",  ["GLOBAL"])
            areas_str = ", ".join(areas) if isinstance(areas, (list, tuple)) else str(areas)
            lines += [
                f"- Name: {name}",
                f"  Type: {typ}",
                f"  Access: {access}",
                f"  Areas: {areas_str}",
                ""
            ]
        return "\n".join(lines)


# ────────────────────────────────────────────────
# Module worker  ← RAG+DSPy CHANGE
# Uses RAGDSPyArchitectAgent instead of ArchitectAgent
# Records per-module metrics for thesis comparison
# ────────────────────────────────────────────────
def _generate_one_module(
    domain: str,
    module_props: list,
    metrics_collector: list,
) -> tuple[str, bool, str | None]:
    """Returns (domain, success, error_msg)."""
    print(f"\n{'=' * 60}")
    print(f" MODULE: {domain.upper()} ({len(module_props)} props)")
    print(f"{'=' * 60}")

    module_spec = ModuleSpec(domain=domain, properties=module_props)
    t_start = time.time()

    try:
        # ← RAG+DSPy CHANGE: RAGDSPyArchitectAgent instead of ArchitectAgent
        agent = RAGDSPyArchitectAgent(
            dspy_programs_dir=DSPY_PROGRAMS_DIR,
            rag_top_k=RAG_TOP_K,
        )
        result = agent.run(module_spec)
        elapsed = time.time() - t_start

        # Collect metrics for thesis results  ← RAG+DSPy CHANGE
        metric_fn = METRIC_REGISTRY.get("aidl")
        score = metric_fn(module_spec, result) if metric_fn and result else 0.0
        metrics_collector.append({
            "condition":       "rag_dspy",
            "stage":           "hal_module",
            "domain":          domain,
            "properties":      len(module_props),
            "generation_time": round(elapsed, 2),
            "metric_score":    round(score, 4),
            "success":         True,
        })

        print(f" [MODULE {domain}] → OK  (score={score:.3f}, {elapsed:.1f}s)")
        return (domain, True, None)

    except Exception as e:
        elapsed = time.time() - t_start
        metrics_collector.append({
            "condition":       "rag_dspy",
            "stage":           "hal_module",
            "domain":          domain,
            "properties":      len(module_props),
            "generation_time": round(elapsed, 2),
            "metric_score":    0.0,
            "success":         False,
            "error":           str(e),
        })
        print(f" [MODULE {domain}] → FAILED: {e}")
        return (domain, False, str(e))


def main():
    OUTPUT_DIR.mkdir(parents=True, exist_ok=True)
    Path("experiments/results").mkdir(parents=True, exist_ok=True)

    # ── Banner  ← RAG+DSPy CHANGE ──────────────────────────────────
    print("=" * 70)
    print("  VSS → AAOS HAL Generation Pipeline  (Condition 3: RAG + DSPy)")
    print("=" * 70)
    print(f"Test signals     : {TEST_SIGNAL_COUNT}")
    print(f"Persistent cache : {PERSISTENT_CACHE_DIR}")
    print(f"Project output   : {OUTPUT_DIR.resolve()}")
    print(f"RAG database     : {RAG_DB_PATH}")
    print(f"DSPy programs    : {DSPY_PROGRAMS_DIR}")
    print()
    print("RAG + DSPy Configuration:")
    print("  - RAG:  Retrieved AOSP source examples injected into every prompt")
    print("  - DSPy: MIPROv2-optimised prompts replace hand-crafted variants")
    print("  - Goal: Higher structural correctness vs conditions 1 & 2")
    print("  - Metrics saved → experiments/results/rag_dspy.json")
    print("=" * 70)
    print()

    # Shared metrics collector — populated by all agents  ← RAG+DSPy CHANGE
    run_metrics: list[dict] = []
    run_start = time.time()

    # ════════════════════════════════════════════════════════════════
    # ADAPTIVE WRAPPER INIT  (same as condition 2)
    # ════════════════════════════════════════════════════════════════
    print("[ADAPTIVE] Initializing adaptive learning components...")
    adaptive_wrapper = get_adaptive_wrapper(
        enable_all=True,
        output_dir="adaptive_outputs_rag_dspy"  # ← RAG+DSPy CHANGE: isolated dir
    )
    print("[ADAPTIVE] Ready — chunk optimizer + prompt selector active")
    print()

    # ════════════════════════════════════════════════════════════════
    # RAG READINESS CHECK  ← RAG+DSPy CHANGE
    # Fail fast if the index hasn't been built yet
    # ════════════════════════════════════════════════════════════════
    print("[RAG] Checking vector index...")
    try:
        retriever = get_retriever(db_path=RAG_DB_PATH)
        if not retriever.is_ready():
            print("[RAG] ERROR: ChromaDB index is empty.")
            print("  Run: python -m rag.aosp_indexer --source aosp_source")
            return
        stats = retriever.collection_stats()
        total_chunks = sum(stats.values())
        print(f"[RAG] Index ready — {total_chunks} chunks across {len(stats)} collections")
        for col, count in stats.items():
            print(f"  {col:<25} {count:>6} chunks")
    except FileNotFoundError as e:
        print(f"[RAG] ERROR: {e}")
        print("  Run: python -m rag.aosp_indexer --source aosp_source")
        return
    print()

    # ════════════════════════════════════════════════════════════════
    # DSPy PROGRAMS CHECK  ← RAG+DSPy CHANGE
    # Warn if optimised programs are missing (will use unoptimised modules)
    # ════════════════════════════════════════════════════════════════
    print("[DSPy] Checking optimised programs...")
    programs_dir = Path(DSPY_PROGRAMS_DIR)
    expected_programs = [
        "aidl_program", "cpp_program", "selinux_program",
        "design_doc_program", "android_app_program", "backend_program",
    ]
    missing_programs = [
        p for p in expected_programs
        if not (programs_dir / p).exists()
    ]
    if missing_programs:
        print(f"[DSPy] WARNING: {len(missing_programs)} optimised program(s) not found:")
        for p in missing_programs:
            print(f"  Missing: {programs_dir / p}")
        print("  Agents will use unoptimised DSPy modules.")
        print("  Run: python dspy_opt/optimizer.py  to generate them.")
    else:
        print(f"[DSPy] All {len(expected_programs)} optimised programs found ✓")
    print()

    # ──────────────────────────────────────────────
    # 1. Load VSS  (identical to other conditions)
    # ──────────────────────────────────────────────
    print(f"[PREP] Loading and flattening {VSS_PATH} ...")
    try:
        with open(VSS_PATH, "r", encoding="utf-8") as f:
            raw_vss = json.load(f)
        all_leaves = flatten_vss(raw_vss)
        print(f" Flattened to {len(all_leaves)} leaf signals")
    except Exception as e:
        print(f"Cannot load or flatten {VSS_PATH}: {e}")
        return

    if len(all_leaves) < TEST_SIGNAL_COUNT:
        print(f"Warning: only {len(all_leaves)} signals available (requested {TEST_SIGNAL_COUNT})")
        selected_signals = all_leaves
    else:
        sorted_paths     = sorted(all_leaves.keys())
        selected_paths   = sorted_paths[:TEST_SIGNAL_COUNT]
        selected_signals = {p: all_leaves[p] for p in selected_paths}

    print(f"Selected {len(selected_signals)} leaf signals for labelling & processing")

    limited_path = PERSISTENT_CACHE_DIR / f"VSS_LIMITED_{TEST_SIGNAL_COUNT}.json"
    limited_path.write_text(
        json.dumps(selected_signals, indent=2, ensure_ascii=False),
        encoding="utf-8"
    )
    print(f" Wrote selected flat subset → {limited_path}")

    # ──────────────────────────────────────────────
    # 2. Label  (identical logic to fixed multi_main files)
    # ──────────────────────────────────────────────
    labelled_path  = PERSISTENT_CACHE_DIR / f"VSS_LABELLED_{TEST_SIGNAL_COUNT}.json"
    need_labelling = True

    if labelled_path.exists():
        if labelled_path.stat().st_mtime >= limited_path.stat().st_mtime:
            print(f"[LABELLING] Using valid cached labelled data: {labelled_path}")
            with open(labelled_path, "r", encoding="utf-8") as f:
                labelled_data = json.load(f)
            print(f" Loaded {len(labelled_data)} labelled signals from cache")
            need_labelling = False
        else:
            print("[LABELLING] Cache outdated → re-labelling")

    if need_labelling:
        print("[LABELLING] Labelling the selected subset (fast mode)...")
        labelling_agent = VSSLabellingAgent()

        MAX_LABEL_RETRIES = 3
        labelled_data = None

        for attempt in range(1, MAX_LABEL_RETRIES + 1):
            result = labelling_agent.run_on_dict(selected_signals)
            if len(result) == len(selected_signals):
                labelled_data = result
                print(f"[LABELLING] All {len(selected_signals)} signals labelled (attempt {attempt})")
                break
            print(f"[LABELLING] Attempt {attempt}: got {len(result)} labels, "
                  f"expected {len(selected_signals)} — retrying...")

        if labelled_data is None:
            print(f"[LABELLING] Batch retries exhausted. "
                  f"Falling back to per-signal labelling...")
            labelled_data = {}
            failed_signals = []
            for sig_path, sig_data in selected_signals.items():
                try:
                    single = labelling_agent.run_on_dict({sig_path: sig_data})
                    if single:
                        labelled_data.update(single)
                    else:
                        failed_signals.append(sig_path)
                except Exception as e:
                    failed_signals.append(sig_path)
                    print(f"[LABELLING] ERROR labelling {sig_path}: {e}")

            if failed_signals:
                print(f"[LABELLING] {len(failed_signals)} signals unlabellable: "
                      f"{failed_signals[:5]}{'...' if len(failed_signals) > 5 else ''}")
            print(f"[LABELLING] Individual fallback: "
                  f"{len(labelled_data)}/{len(selected_signals)} labelled")

        labelled_path.write_text(
            json.dumps(labelled_data, indent=2, ensure_ascii=False),
            encoding="utf-8"
        )
        print(f"[LABELLING] Saved → {labelled_path}")

    # Hard validation
    missing_labels = set(selected_signals.keys()) - set(labelled_data.keys())
    if missing_labels:
        print(f"[LABELLING] ERROR: {len(missing_labels)} signals unlabelled — skipping them.")
        print(f"  Missing: {list(missing_labels)[:5]}{'...' if len(missing_labels) > 5 else ''}")
        selected_signals = {k: v for k, v in selected_signals.items() if k in labelled_data}
        print(f"  Proceeding with {len(selected_signals)} signals.")
    else:
        print(f"[LABELLING] Done! {len(labelled_data)} labelled signals ready")

    # ──────────────────────────────────────────────
    # 3. Convert labelled data → YAML  (identical)
    # ──────────────────────────────────────────────
    print("\n[YAML] Converting labelled subset to HAL YAML spec...")
    yaml_spec, prop_count = vss_to_yaml_spec(
        vss_json_path=str(labelled_path),
        include_prefixes=None,
        max_props=None,
        vendor_namespace=VENDOR_NAMESPACE,
        add_meta=True,
    )
    spec_path = OUTPUT_DIR / f"SPEC_FROM_VSS_{TEST_SIGNAL_COUNT}.yaml"
    spec_path.write_text(yaml_spec, encoding="utf-8")
    print(f" Wrote {spec_path} with {prop_count} properties")

    # ──────────────────────────────────────────────
    # 4. Load spec  (identical)
    # ──────────────────────────────────────────────
    print("[LOAD] Loading HAL spec...")
    try:
        full_spec = load_hal_spec_from_yaml_text(yaml_spec)
        print(f"[LOAD] Success — domain: {full_spec.domain}, "
              f"{len(full_spec.properties)} properties")
    except Exception as e:
        print(f"[LOAD ERROR] Failed: {e}")
        return

    properties_by_id: dict = {}
    for idx, p in enumerate(full_spec.properties):
        name = getattr(p, "id", None)
        if name is None:
            print(f"[WARNING] Property #{idx} missing 'id': {p}")
            continue
        if name in properties_by_id:
            print(f"[WARNING] Duplicate id: {name}")
        properties_by_id[name] = p

    print(f"[LOAD] Built lookup with {len(properties_by_id)} unique ids")
    print("Sample loaded property ids (first 5 or less):")
    for name in list(properties_by_id.keys())[:5]:
        print(f"  → {name}")
    if not properties_by_id:
        print("  (no properties loaded — check YAML or loader)")

    # ──────────────────────────────────────────────
    # 5. Module planning  (identical)
    # ──────────────────────────────────────────────
    print("\n[PLAN] Running Module Planner...")
    try:
        module_signal_map = plan_modules_from_spec(yaml_spec)
        total = sum(len(v) for v in module_signal_map.values())
        print(f" → {len(module_signal_map)} modules, {total} signals total")
        print(" Modules:", ", ".join(sorted(module_signal_map.keys())) or "(none)")
    except Exception as e:
        print(f"[ERROR] Planner failed: {e}")
        return

    print("\n[DEBUG] Checking for name format mismatch:")
    if module_signal_map and properties_by_id:
        first_module  = next(iter(module_signal_map))
        planner_name  = module_signal_map[first_module][0] if module_signal_map[first_module] else None
        loader_name   = list(properties_by_id.keys())[0]
        print(f"  Planner format : {planner_name}")
        print(f"  Loader format  : {loader_name}")
        print(f"  Match          : {planner_name == loader_name}")

    print("\nSample planner property names (first module):")
    if module_signal_map:
        first_module = next(iter(module_signal_map))
        names = module_signal_map[first_module]
        for name in names[:5]:
            print(f"  → {name}")
        if len(names) > 5:
            print(f"  ... +{len(names) - 5} more")

    # ──────────────────────────────────────────────
    # 6. Generate HAL modules  ← RAG+DSPy CHANGE
    # Uses RAGDSPyArchitectAgent via _generate_one_module
    # Passes metrics_collector to capture per-module scores
    # ──────────────────────────────────────────────
    print(f"\n[GEN] Generating {len(module_signal_map)} HAL modules "
          f"(parallel, max {MAX_PARALLEL_LLM_CALLS}) [RAG+DSPy]...")

    tasks_to_submit: list[tuple[str, list]] = []
    total_planned = 0
    total_matched = 0

    for domain, signal_names in module_signal_map.items():
        total_planned += len(signal_names)
        if not signal_names:
            continue

        module_props = []
        missing      = []
        for name in signal_names:
            prop = properties_by_id.get(name)
            if prop:
                module_props.append(prop)
                total_matched += 1
            else:
                missing.append(name)

        print(f"  {domain}: matched {len(module_props)}/{len(signal_names)} properties")
        if missing:
            print(f"    Missing ({len(missing)}): "
                  f"{missing[:5]}{'...' if len(missing) > 5 else ''}")

        if not module_props:
            print(f"  Skipping {domain} — no matching properties "
                  f"(empty module, no files will be generated)")
            continue

        tasks_to_submit.append((domain, module_props))

    generated_count = 0
    with ThreadPoolExecutor(max_workers=MAX_PARALLEL_LLM_CALLS) as pool:
        futures = {
            pool.submit(_generate_one_module, domain, props, run_metrics): domain
            for domain, props in tasks_to_submit
        }
        for future in as_completed(futures):
            domain, success, error = future.result()
            if success:
                generated_count += 1

    print(f"\nAll HAL module drafts generated "
          f"({generated_count}/{len(tasks_to_submit)} modules OK)")
    if total_planned > 0:
        print(f"Overall match rate: {total_matched}/{total_planned} properties "
              f"({total_matched / total_planned * 100:.1f}%)")

    # ──────────────────────────────────────────────
    # 7. Supporting components  ← RAG+DSPy CHANGE
    # Same dependency structure as condition 2 but all agents
    # replaced with their RAG+DSPy variants
    # ──────────────────────────────────────────────
    print("\n[SUPPORT] Generating supporting components (RAG + DSPy mode)...")
    print("  RAG: real AOSP examples retrieved per component")
    print("  DSPy: optimised prompts from MIPROv2 optimisation")
    print()

    def _run_design_doc():
        # ← RAG+DSPy CHANGE: RAGDSPyDesignDocAgent
        t0 = time.time()
        agent = RAGDSPyDesignDocAgent(
            dspy_programs_dir=DSPY_PROGRAMS_DIR,
            rag_top_k=RAG_TOP_K,
        )
        agent.run(module_signal_map, full_spec.properties, yaml_spec)
        elapsed = time.time() - t0
        run_metrics.append({
            "condition": "rag_dspy", "stage": "design_doc",
            "generation_time": round(elapsed, 2), "success": True,
        })

    def _run_selinux():
        # ← RAG+DSPy CHANGE: RAGDSPySELinuxAgent
        t0 = time.time()
        agent = RAGDSPySELinuxAgent(
            dspy_programs_dir=DSPY_PROGRAMS_DIR,
            rag_top_k=RAG_TOP_K,
        )
        agent.run(full_spec)
        elapsed = time.time() - t0
        run_metrics.append({
            "condition": "rag_dspy", "stage": "selinux",
            "generation_time": round(elapsed, 2), "success": True,
        })

    def _run_android_app():
        # ← RAG+DSPy CHANGE: RAGDSPyAndroidAppAgent
        t0 = time.time()
        agent = RAGDSPyAndroidAppAgent(
            dspy_programs_dir=DSPY_PROGRAMS_DIR,
            rag_top_k=RAG_TOP_K,
        )
        agent.run(module_signal_map, full_spec.properties)
        elapsed = time.time() - t0
        run_metrics.append({
            "condition": "rag_dspy", "stage": "android_app",
            "generation_time": round(elapsed, 2), "success": True,
        })

    def _run_backend():
        # ← RAG+DSPy CHANGE: RAGDSPyBackendAgent
        t0 = time.time()
        agent = RAGDSPyBackendAgent(
            dspy_programs_dir=DSPY_PROGRAMS_DIR,
            rag_top_k=RAG_TOP_K,
        )
        agent.run(module_signal_map, full_spec.properties)
        elapsed = time.time() - t0
        run_metrics.append({
            "condition": "rag_dspy", "stage": "backend",
            "generation_time": round(elapsed, 2), "success": True,
        })

    # Group A — all independent, run together
    group_a_tasks = [
        ("DesignDoc",  _run_design_doc),
        ("SELinux",    _run_selinux),
        ("AndroidApp", _run_android_app),
        ("Backend",    _run_backend),
    ]

    with ThreadPoolExecutor(max_workers=len(group_a_tasks)) as pool:
        futures = {pool.submit(fn): name for name, fn in group_a_tasks}
        for future in as_completed(futures):
            name = futures[future]
            try:
                future.result()
                print(f"  [SUPPORT] {name} → OK")
            except Exception as e:
                print(f"  [SUPPORT] {name} → FAILED: {e}")
                run_metrics.append({
                    "condition": "rag_dspy", "stage": name.lower(),
                    "success": False, "error": str(e),
                })

    # Group B — sequential chain (identical to other conditions)
    print("  [SUPPORT] Running PromoteDraft → BuildGlue (sequential, order matters)...")
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

            class LLMClientWrapper:
                def generate(self, prompt, timeout=300):
                    try:
                        return call_llm(prompt, timeout=timeout)
                    except TypeError:
                        return call_llm(prompt)

            llm_client = LLMClientWrapper()
            print(f"  [BUILD GLUE] LLM client loaded (using call_llm)")
        except (ImportError, Exception) as e:
            print(f"  [BUILD GLUE] LLM client not available: {e}")
            print(f"  [BUILD GLUE] Will use template-based generation")

        if llm_client:
            print(f"  [BUILD GLUE] Using LLM-based generation "
                  f"(timeout: {BUILD_GLUE_LLM_TIMEOUT}s)")
            build_agent = ImprovedBuildGlueAgent(
                output_root=str(OUTPUT_DIR),
                module_plan=str(module_plan_path) if module_plan_path.exists() else None,
                hal_spec=str(spec_path) if spec_path.exists() else None,
                llm_client=llm_client,
                timeout=BUILD_GLUE_LLM_TIMEOUT,
            )
        else:
            print(f"  [BUILD GLUE] Using template-based generation")
            build_agent = BuildGlueAgent(
                output_root=str(OUTPUT_DIR),
                module_plan=str(module_plan_path) if module_plan_path.exists() else None,
                hal_spec=str(spec_path) if spec_path.exists() else None,
            )

        success = build_agent.run()
        if success:
            is_valid, errors = build_agent.validate()
            if not is_valid:
                print(f"  [SUPPORT] BuildGlue → OK (with validation warnings)")
                print(f"           Warnings: {', '.join(errors)}")
            else:
                print("  [SUPPORT] BuildGlue → OK (validated ✓)")
            run_metrics.append({
                "condition": "rag_dspy", "stage": "build_glue",
                "success": True, "validated": is_valid,
            })
        else:
            print("  [SUPPORT] BuildGlue → FAILED")
            run_metrics.append({
                "condition": "rag_dspy", "stage": "build_glue", "success": False,
            })

    except Exception as e:
        print(f"  [SUPPORT] BuildGlue → FAILED: {e}")
        import traceback
        traceback.print_exc()

    # ════════════════════════════════════════════════════════════════
    # ADAPTIVE STATISTICS  (same as condition 2)
    # ════════════════════════════════════════════════════════════════
    print("\n[ADAPTIVE] Learning statistics this run:")
    stats   = adaptive_wrapper.get_full_statistics()
    tracker = stats["tracker"]
    print(f"  Total generations tracked : {tracker['total_generations']}")
    print(f"  Overall success rate      : {tracker['overall_success_rate']:.1%}")
    print(f"  Avg quality score         : {tracker['avg_quality']:.2f}")
    print(f"  Avg generation time       : {tracker['avg_generation_time']:.1f}s")

    if stats["chunk_optimizer"]:
        chunk = stats["chunk_optimizer"]
        print(f"  Best chunk size learned   : {chunk['best_chunk_size']}")

    if stats["prompt_selector"]:
        perf = stats["prompt_selector"].get("overall_performance", {})
        if perf:
            best = max(perf.items(), key=lambda x: x[1]["success_rate"])
            print(f"  Best prompt variant       : {best[0]} ({best[1]['success_rate']:.1%})")

    adaptive_wrapper.export_results(
        f"adaptive_outputs_rag_dspy/thesis_results.json"
    )

    # ════════════════════════════════════════════════════════════════
    # THESIS METRICS EXPORT  ← RAG+DSPy CHANGE
    # Saves structured results for experiments/analyze_results.py
    # ════════════════════════════════════════════════════════════════
    total_elapsed = time.time() - run_start

    # Compute aggregate metric scores across all stages
    successful = [m for m in run_metrics if m.get("success")]
    scores     = [m["metric_score"] for m in run_metrics if "metric_score" in m]
    times      = [m["generation_time"] for m in run_metrics if "generation_time" in m]

    experiment_summary = {
        "condition":            "rag_dspy",
        "timestamp":            time.strftime("%Y-%m-%dT%H:%M:%S"),
        "total_signals":        TEST_SIGNAL_COUNT,
        "total_modules":        len(tasks_to_submit),
        "modules_succeeded":    generated_count,
        "total_run_time_s":     round(total_elapsed, 1),
        "avg_metric_score":     round(sum(scores) / len(scores), 4) if scores else 0.0,
        "avg_generation_time_s":round(sum(times)  / len(times),  2) if times  else 0.0,
        "stages_succeeded":     len(successful),
        "stages_total":         len(run_metrics),
        "rag_top_k":            RAG_TOP_K,
        "dspy_optimised":       len(missing_programs) == 0,
        "per_stage_metrics":    run_metrics,
        "adaptive_stats":       tracker,
    }

    metrics_path = Path(METRICS_OUTPUT_PATH)
    metrics_path.parent.mkdir(parents=True, exist_ok=True)
    metrics_path.write_text(
        json.dumps(experiment_summary, indent=2),
        encoding="utf-8"
    )
    print(f"\n[METRICS] Condition 3 results saved → {metrics_path}")

    # ════════════════════════════════════════════════════════════════
    # DYNAMIC QUALITY SUMMARY  ← RAG+DSPy CHANGE
    # Uses real tracked success rate (same fix as adaptive version)
    # ════════════════════════════════════════════════════════════════
    actual_rate = tracker.get("overall_success_rate", None)
    print("\n" + "=" * 70)
    print("  Generation Complete! (Condition 3: RAG + DSPy)")
    print("=" * 70)
    print(f"Cached input files  : {PERSISTENT_CACHE_DIR}")
    print(f"Generated outputs   : {OUTPUT_DIR.resolve()}")
    print(f"Thesis metrics      : {metrics_path.resolve()}")
    print()
    print("RAG + DSPy Results Summary:")
    print("  Check agent output above for detailed statistics:")
    print("    - [RAG+DSPy AIDL]       → retrieval scores & AIDL quality")
    print("    - [RAG+DSPy ANDROID APP]→ Kotlin fragment quality")
    print("    - [RAG+DSPy BACKEND]    → FastAPI quality")
    print("    - [RAG+DSPy DESIGN DOC] → documentation quality")
    print()

    if actual_rate is not None:
        pct = actual_rate * 100
        if   pct >= 90: tier, symbol = "Excellent", "✓"
        elif pct >= 80: tier, symbol = "Good",      "✓"
        elif pct >= 70: tier, symbol = "Fair",      "⚠"
        else:           tier, symbol = "Poor",      "✗"
        print(f"Actual Quality This Run : {symbol} {tier} ({pct:.1f}% success rate)")
        print()

    print("Quality Tiers for Reference:")
    print("  ✓ Excellent (90%+): Strong RAG+DSPy improvement over baseline")
    print("  ✓ Good     (80-89%): Moderate improvement, check retrieval quality")
    print("  ⚠ Fair     (70-79%): Marginal improvement — tune RAG_TOP_K or re-run optimizer")
    print("  ✗ Poor      (<70%): Check RAG index quality and DSPy metric definitions")
    print()
    print("Next Steps:")
    print("  1. Compare experiments/results/ across all 3 conditions")
    print("  2. Run: python experiments/analyze_results.py")
    print("  3. Check RAG retrieval scores in agent logs (score= fields)")
    print("  4. If DSPy programs missing, run: python dspy_opt/optimizer.py")
    print("=" * 70)


if __name__ == "__main__":
    main()