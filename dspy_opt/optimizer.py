"""
dspy_opt/optimizer.py
═══════════════════════════════════════════════════════════════════
MIPROv2 prompt optimiser for all HAL generation agents.

Runs ONCE before the condition 3 experiment. Reads your existing
labelled VSS signals as training data, optimises each agent's
prompt using DSPy's MIPROv2 algorithm, and saves the result to
dspy_opt/saved/<agent_type>_program/.

Run order:
  1. python multi_main_adaptive.py       # generates labelled signals
  2. python dspy_opt/optimizer.py        # THIS FILE — optimises prompts
  3. python multi_main_rag_dspy.py       # uses optimised prompts

Usage:
  # Optimise all agents (recommended for thesis)
  python dspy_opt/optimizer.py

  # Optimise specific agents only
  python dspy_opt/optimizer.py --agents aidl selinux android_app

  # Use fewer training examples (faster, less optimal)
  python dspy_opt/optimizer.py --train-size 10

  # Force re-optimise even if saved programs exist
  python dspy_opt/optimizer.py --force

Expected runtime: 30-90 minutes total for all 12 agents on
qwen2.5-coder:32b local model (depends on hardware).
Each agent optimises independently, so you can run a subset
and re-run the rest later.
═══════════════════════════════════════════════════════════════════
"""

from __future__ import annotations

import argparse
import json
import logging
import time
from pathlib import Path
from typing import Optional

import dspy

from dspy_opt.hal_modules  import MODULE_REGISTRY, _BaseHALModule
from dspy_opt.metrics      import METRIC_REGISTRY
from dspy_opt.validators   import print_availability_report
from rag.aosp_retriever    import get_retriever, COLLECTION_MAP

logger = logging.getLogger(__name__)

# ─────────────────────────────────────────────────────────────────
# Configuration
# ─────────────────────────────────────────────────────────────────

# Local Ollama endpoint for qwen2.5-coder:32b
LLM_MODEL    = "ollama/qwen2.5-coder:32b"
LLM_API_BASE = "http://localhost:11434"

# Where labelled VSS signals are cached by multi_main_adaptive.py
DEFAULT_LABELLED_PATH = "/content/vss_temp/VSS_LABELLED_50.json"

# Where optimised programs are saved
DEFAULT_PROGRAMS_DIR  = "dspy_opt/saved"

# Default training set size (number of VSS signals used as examples)
# 15-20 is a practical default for MIPROv2 with a large local LLM
DEFAULT_TRAIN_SIZE = 15

# MIPROv2 settings — "medium" balances quality vs optimisation time
# Options: "light" (fast, lower quality), "medium", "heavy" (best, slow)
MIPRO_AUTO_SETTING = "medium"

# Max bootstrapped demonstrations MIPROv2 will try
MAX_BOOTSTRAPPED_DEMOS = 3

# Max labelled demonstrations to include in optimised prompt
MAX_LABELED_DEMOS = 5

# RAG settings for building training examples
RAG_DB_PATH = "rag/chroma_db"
RAG_TOP_K   = 3


# ─────────────────────────────────────────────────────────────────
# Training example builder
# ─────────────────────────────────────────────────────────────────

class TrainingSetBuilder:
    """
    Builds dspy.Example training sets from labelled VSS signals.

    Uses your existing pipeline's labelled data — no new data collection
    needed. Each signal becomes one training example with:
      - Inputs:  domain, properties, aosp_context (from RAG)
      - No gold output — MIPROv2 uses metric functions instead
    """

    def __init__(self, labelled_path: str, retriever=None):
        self.labelled_path = Path(labelled_path)
        self.retriever     = retriever

        if not self.labelled_path.exists():
            raise FileNotFoundError(
                f"Labelled signals not found: {self.labelled_path}\n"
                f"Run multi_main_adaptive.py first to generate them."
            )

        with open(self.labelled_path, "r", encoding="utf-8") as f:
            self.labelled_data: dict = json.load(f)

        logger.info(
            f"[Optimizer] Loaded {len(self.labelled_data)} labelled signals "
            f"from {self.labelled_path}"
        )

    def _extract_domain(self, sig_path: str, sig_data: dict) -> str:
        """Extract domain from signal path or labelled data."""
        domain = sig_data.get("domain", "")
        if domain:
            return domain.upper()
        # Fallback: parse from path e.g. VEHICLE_ADAS_ABS_ISENABLED → ADAS
        parts = sig_path.upper().split("_")
        if "CHILDREN" in parts:
            idx = parts.index("CHILDREN")
            if idx > 0:
                return parts[idx - 1]
        if len(parts) >= 2:
            return parts[1]
        return "UNKNOWN"

    def _build_properties_text(self, sig_path: str, sig_data: dict) -> str:
        """Format a single signal as a property spec string."""
        return (
            f"- Name: {sig_path}\n"
            f"  Type: {sig_data.get('type', 'BOOLEAN')}\n"
            f"  Access: {sig_data.get('access', 'READ')}\n"
            f"  Areas: {sig_data.get('areas', '')}"
        )

    def _get_rag_context(self, query: str, agent_type: str) -> str:
        """Retrieve RAG context for a training example."""
        if self.retriever is None:
            return ""
        try:
            results = self.retriever.retrieve(
                query, agent_type=agent_type, top_k=RAG_TOP_K
            )
            return self.retriever.format_for_prompt(results)
        except Exception as e:
            logger.debug(f"[Optimizer] RAG retrieval failed: {e}")
            return ""

    def build(
        self,
        agent_type: str,
        n: int = DEFAULT_TRAIN_SIZE,
    ) -> list[dspy.Example]:
        """
        Build training examples for a specific agent_type.

        Groups signals into mini-modules (up to 5 signals each) so
        examples reflect real generation conditions, then creates
        one dspy.Example per group.

        Parameters
        ----------
        agent_type : str  — key from MODULE_REGISTRY
        n          : int  — max number of training examples to generate

        Returns
        -------
        list[dspy.Example]
        """
        items   = list(self.labelled_data.items())
        # Group signals into batches of up to 5 (matches real pipeline chunks)
        groups  = [items[i:i+5] for i in range(0, len(items), 5)][:n]

        examples = []
        for group in groups:
            # Build a mini property spec from all signals in the group
            domain_counts: dict[str, int] = {}
            prop_lines    = []

            for sig_path, sig_data in group:
                domain = self._extract_domain(sig_path, sig_data)
                domain_counts[domain] = domain_counts.get(domain, 0) + 1
                prop_lines.append(self._build_properties_text(sig_path, sig_data))

            # Use the most common domain in this group
            domain     = max(domain_counts, key=domain_counts.get)
            properties = "\n".join(prop_lines)

            # Build RAG query based on agent type
            query = self._build_query(agent_type, domain, group)
            aosp_context = self._get_rag_context(query, agent_type)

            # Build input kwargs — varies slightly by agent_type
            inputs = self._build_inputs(
                agent_type, domain, properties, aosp_context
            )

            example = dspy.Example(**inputs).with_inputs(*inputs.keys())
            examples.append(example)

        logger.info(
            f"[Optimizer] Built {len(examples)} training examples "
            f"for agent_type='{agent_type}'"
        )
        return examples

    def _build_query(
        self,
        agent_type: str,
        domain: str,
        group: list,
    ) -> str:
        """Build a RAG query string appropriate for the agent_type."""
        prop_types = " ".join(
            g[1].get("type", "") for g in group[:3]
        )
        queries = {
            "aidl":           f"{domain} AIDL interface {prop_types} VHAL",
            "cpp":            f"{domain} VHAL C++ service implementation {prop_types}",
            "selinux":        f"{domain} SELinux HAL policy vhal vendor",
            "build":          f"{domain} Android.bp aidl_interface cc_binary vendor",
            "vintf":          f"{domain} VINTF manifest HAL version init.rc",
            "design_doc":     f"{domain} HAL design document architecture overview",
            "puml":           f"{domain} AOSP architecture diagram component",
            "android_app":    f"CarPropertyManager {domain} Kotlin Fragment {prop_types}",
            "android_layout": f"Android layout {domain} property display TextView Switch",
            "backend":        f"FastAPI REST {domain} property server async Python",
            "backend_model":  f"Pydantic model {domain} property {prop_types}",
            "simulator":      f"Python simulator {domain} property random asyncio",
        }
        return queries.get(agent_type, f"{domain} {agent_type}")

    def _build_inputs(
        self,
        agent_type: str,
        domain: str,
        properties: str,
        aosp_context: str,
    ) -> dict:
        """
        Build the input dict for a dspy.Example based on agent_type.
        Must match the InputFields defined in the Signature.
        """
        # Most agents share these three inputs
        base = {
            "domain":       domain,
            "properties":   properties,
            "aosp_context": aosp_context,
        }

        # Agents with different input signatures
        overrides = {
            "selinux": {
                "domain":       domain,
                "service_name": f"vendor.vss.{domain.lower()}",
                "aosp_context": aosp_context,
            },
            "build": {
                "module_name":  f"vendor.vss.{domain.lower()}",
                "dependencies": "libvhalclient libbinder_ndk libbase",
                "aosp_context": aosp_context,
            },
            "vintf": {
                "domain":       domain,
                "hal_version":  "2",
                "aosp_context": aosp_context,
            },
            "design_doc": {
                "domain":   domain,
                "modules":  f"{domain}: {properties.count('Name:')  } signals",
                "aosp_context": aosp_context,
            },
            "puml": {
                "domain":     domain,
                "components": f"VSS → {domain}HAL → CarService → AndroidApp",
                "aosp_context": aosp_context,
            },
            "backend_model": {
                "properties":   properties,
                "aosp_context": aosp_context,
            },
        }

        return overrides.get(agent_type, base)


# ─────────────────────────────────────────────────────────────────
# Optimiser
# ─────────────────────────────────────────────────────────────────

class HALPromptOptimizer:
    """
    Runs DSPy MIPROv2 optimisation for each HAL generation agent.

    For each agent_type:
      1. Builds training examples from labelled VSS signals
      2. Runs MIPROv2.compile() — rewrites prompts + selects demos
      3. Saves the optimised program to dspy_opt/saved/<type>_program/
      4. Records result summary in dspy_opt/saved/optimization_log.json
    """

    def __init__(
        self,
        labelled_path:    str  = DEFAULT_LABELLED_PATH,
        programs_dir:     str  = DEFAULT_PROGRAMS_DIR,
        lm_model:         str  = LLM_MODEL,
        lm_api_base:      str  = LLM_API_BASE,
        rag_db_path:      str  = RAG_DB_PATH,
        train_size:       int  = DEFAULT_TRAIN_SIZE,
        mipro_auto:       str  = MIPRO_AUTO_SETTING,
        force_reoptimise: bool = False,
    ):
        self.programs_dir     = Path(programs_dir)
        self.programs_dir.mkdir(parents=True, exist_ok=True)
        self.train_size       = train_size
        self.mipro_auto       = mipro_auto
        self.force_reoptimise = force_reoptimise
        self.opt_log: list[dict] = []

        # Configure DSPy LM — connects to your existing local Ollama instance
        print(f"[Optimizer] Connecting to LLM: {lm_model} @ {lm_api_base}")
        lm = dspy.LM(lm_model, api_base=lm_api_base, cache=False)
        dspy.configure(lm=lm)
        print(f"[Optimizer] LM configured ✓")

        # Initialise RAG retriever for training example context
        retriever = None
        try:
            retriever = get_retriever(db_path=rag_db_path)
            if retriever.is_ready():
                print(f"[Optimizer] RAG retriever ready ✓")
            else:
                print(f"[Optimizer] WARNING: RAG index empty — "
                      f"training examples will have no AOSP context")
        except Exception as e:
            print(f"[Optimizer] WARNING: RAG not available ({e}) — "
                  f"training without retrieval context")

        # Training set builder
        self.builder = TrainingSetBuilder(
            labelled_path=labelled_path,
            retriever=retriever,
        )

    def optimise_all(
        self,
        agent_types: Optional[list[str]] = None,
    ) -> dict[str, dict]:
        """
        Optimise all registered agents (or a subset).

        Parameters
        ----------
        agent_types : list[str] or None
            Specific agent types to optimise. None = all registered types.

        Returns
        -------
        dict mapping agent_type → {success, score, time_s, path}
        """
        targets = agent_types or list(MODULE_REGISTRY.keys())
        results = {}

        print(f"\n[Optimizer] Optimising {len(targets)} agents: {targets}")
        print(f"  Training examples per agent : {self.train_size}")
        print(f"  MIPROv2 setting             : {self.mipro_auto}")
        print(f"  Programs saved to           : {self.programs_dir}")
        print()
        print_availability_report()

        for agent_type in targets:
            result = self.optimise_one(agent_type)
            results[agent_type] = result

        # Save optimisation log
        self._save_log(results)
        self._print_summary(results)
        return results

    def optimise_one(self, agent_type: str) -> dict:
        """
        Optimise a single agent and save the result.

        Returns
        -------
        dict with keys: success, score, time_s, path, error
        """
        save_path = self.programs_dir / f"{agent_type}_program"

        # Skip if already optimised and --force not set
        if not self.force_reoptimise and (save_path / "program.json").exists():
            print(f"[{agent_type}] Already optimised — skipping "
                  f"(use --force to re-run)")
            return {
                "success": True, "skipped": True,
                "path": str(save_path), "score": None, "time_s": 0,
            }

        print(f"\n[{agent_type}] Starting optimisation...")
        t_start = time.time()

        try:
            # 1. Get module and metric
            module_class, _ = MODULE_REGISTRY[agent_type]
            module          = module_class()
            metric_fn       = METRIC_REGISTRY.get(agent_type)

            if metric_fn is None:
                raise ValueError(f"No metric registered for '{agent_type}'")

            # 2. Build training set
            trainset = self.builder.build(agent_type, n=self.train_size)
            if not trainset:
                raise ValueError("Empty training set — check labelled signals file")

            print(f"[{agent_type}] Training set: {len(trainset)} examples")

            # 3. Score unoptimised baseline on first few examples
            baseline_scores = self._evaluate_sample(
                module, metric_fn, trainset[:3], agent_type
            )
            avg_baseline = (
                sum(baseline_scores) / len(baseline_scores)
                if baseline_scores else 0.0
            )
            print(f"[{agent_type}] Baseline score: {avg_baseline:.3f}")

            # 4. Run MIPROv2 optimisation
            print(f"[{agent_type}] Running MIPROv2 ({self.mipro_auto})...")
            optimizer = dspy.MIPROv2(
                metric=metric_fn,
                auto=self.mipro_auto,
                num_threads=2,        # conservative for large local LLM
                verbose=False,
            )

            optimised_module = optimizer.compile(
                module,
                trainset=trainset,
                max_bootstrapped_demos=MAX_BOOTSTRAPPED_DEMOS,
                max_labeled_demos=MAX_LABELED_DEMOS,
                requires_permission_to_run=False,
            )

            # 5. Score optimised module
            optimised_scores = self._evaluate_sample(
                optimised_module, metric_fn, trainset[:3], agent_type
            )
            avg_optimised = (
                sum(optimised_scores) / len(optimised_scores)
                if optimised_scores else 0.0
            )
            improvement = avg_optimised - avg_baseline
            print(f"[{agent_type}] Optimised score: {avg_optimised:.3f} "
                  f"(Δ{improvement:+.3f} vs baseline)")

            # 6. Save optimised program
            optimised_module.save(save_path)
            elapsed = time.time() - t_start
            print(f"[{agent_type}] ✓ Done in {elapsed:.1f}s → {save_path}")

            return {
                "success":         True,
                "skipped":         False,
                "agent_type":      agent_type,
                "baseline_score":  round(avg_baseline,   4),
                "optimised_score": round(avg_optimised,  4),
                "improvement":     round(improvement,    4),
                "train_examples":  len(trainset),
                "time_s":          round(elapsed, 1),
                "path":            str(save_path),
            }

        except Exception as e:
            elapsed = time.time() - t_start
            logger.error(f"[{agent_type}] Optimisation failed: {e}", exc_info=True)
            print(f"[{agent_type}] ✗ FAILED after {elapsed:.1f}s: {e}")
            return {
                "success":    False,
                "skipped":    False,
                "agent_type": agent_type,
                "error":      str(e),
                "time_s":     round(elapsed, 1),
            }

    def _evaluate_sample(
        self,
        module:    _BaseHALModule,
        metric_fn: callable,
        examples:  list[dspy.Example],
        agent_type: str,
    ) -> list[float]:
        """
        Run the module on a small sample and return metric scores.
        Catches individual failures so one bad example doesn't abort.
        """
        scores = []
        for ex in examples:
            try:
                inputs = {k: getattr(ex, k) for k in ex.inputs()}
                pred   = module(**inputs)
                score  = metric_fn(ex, pred)
                scores.append(score)
            except Exception as e:
                logger.debug(
                    f"[{agent_type}] Evaluation sample failed: {e}"
                )
                scores.append(0.0)
        return scores

    def _save_log(self, results: dict[str, dict]) -> None:
        """Save a JSON log of all optimisation results."""
        log = {
            "optimised_at":    time.strftime("%Y-%m-%dT%H:%M:%S"),
            "lm_model":        LLM_MODEL,
            "mipro_auto":      self.mipro_auto,
            "train_size":      self.train_size,
            "agents":          results,
        }
        log_path = self.programs_dir / "optimization_log.json"
        log_path.write_text(
            json.dumps(log, indent=2, ensure_ascii=False),
            encoding="utf-8",
        )
        print(f"\n[Optimizer] Log saved → {log_path}")

    def _print_summary(self, results: dict[str, dict]) -> None:
        """Print a formatted optimisation summary table."""
        print("\n" + "=" * 65)
        print("  DSPy MIPROv2 Optimisation Summary")
        print("=" * 65)
        print(f"  {'Agent':<20} {'Baseline':>8} {'Optimised':>9} {'Δ':>6} {'Time':>7}")
        print("  " + "-" * 55)
        for agent_type, r in results.items():
            if r.get("skipped"):
                print(f"  {agent_type:<20} {'(cached)':>8}")
            elif r.get("success"):
                print(
                    f"  {agent_type:<20} "
                    f"{r.get('baseline_score',  0):.3f}    "
                    f"{r.get('optimised_score', 0):.3f}    "
                    f"{r.get('improvement',     0):+.3f}  "
                    f"{r.get('time_s', 0):>5.0f}s"
                )
            else:
                print(f"  {agent_type:<20} FAILED: {r.get('error','')[:30]}")
        print("=" * 65)
        succeeded = sum(1 for r in results.values()
                        if r.get("success") and not r.get("skipped"))
        print(f"  Optimised: {succeeded}/{len(results)} agents")
        print(f"  Programs → {self.programs_dir.resolve()}")
        print("=" * 65)


# ─────────────────────────────────────────────────────────────────
# CLI entry point
# ─────────────────────────────────────────────────────────────────

if __name__ == "__main__":
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s %(levelname)s: %(message)s",
        datefmt="%H:%M:%S",
    )

    parser = argparse.ArgumentParser(
        description="Run MIPROv2 prompt optimisation for all HAL generation agents"
    )
    parser.add_argument(
        "--agents",
        nargs="+",
        choices=list(MODULE_REGISTRY.keys()),
        default=None,
        help="Agent types to optimise (default: all)",
    )
    parser.add_argument(
        "--labelled",
        default=DEFAULT_LABELLED_PATH,
        help=f"Path to labelled VSS signals JSON (default: {DEFAULT_LABELLED_PATH})",
    )
    parser.add_argument(
        "--programs-dir",
        default=DEFAULT_PROGRAMS_DIR,
        help=f"Directory to save optimised programs (default: {DEFAULT_PROGRAMS_DIR})",
    )
    parser.add_argument(
        "--train-size",
        type=int,
        default=DEFAULT_TRAIN_SIZE,
        help=f"Number of training examples per agent (default: {DEFAULT_TRAIN_SIZE})",
    )
    parser.add_argument(
        "--mipro-auto",
        choices=["light", "medium", "heavy"],
        default=MIPRO_AUTO_SETTING,
        help=f"MIPROv2 intensity setting (default: {MIPRO_AUTO_SETTING})",
    )
    parser.add_argument(
        "--rag-db",
        default=RAG_DB_PATH,
        help=f"ChromaDB path (default: {RAG_DB_PATH})",
    )
    parser.add_argument(
        "--force",
        action="store_true",
        help="Re-optimise even if saved programs already exist",
    )
    parser.add_argument(
        "--lm-model",
        default=LLM_MODEL,
        help=f"DSPy LM model string (default: {LLM_MODEL})",
    )
    parser.add_argument(
        "--lm-api-base",
        default=LLM_API_BASE,
        help=f"LM API base URL (default: {LLM_API_BASE})",
    )

    args = parser.parse_args()

    optimiser = HALPromptOptimizer(
        labelled_path    = args.labelled,
        programs_dir     = args.programs_dir,
        lm_model         = args.lm_model,
        lm_api_base      = args.lm_api_base,
        rag_db_path      = args.rag_db,
        train_size       = args.train_size,
        mipro_auto       = args.mipro_auto,
        force_reoptimise = args.force,
    )

    optimiser.optimise_all(agent_types=args.agents)