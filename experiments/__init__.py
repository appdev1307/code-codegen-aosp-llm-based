"""
experiments/
═══════════════════════════════════════════════════════════════════
Thesis experiment runner and analysis tools.

Compares three pipeline conditions on the same 50 VSS signals:

  Condition 1 — Baseline
      Entry point : multi_main.py
      Agents      : Original hand-crafted prompt agents
      Results     : experiments/results/baseline.json

  Condition 2 — Adaptive
      Entry point : multi_main_adaptive.py
      Agents      : Thompson Sampling + prompt variant selection
      Results     : experiments/results/adaptive.json

  Condition 3 — RAG + DSPy
      Entry point : multi_main_rag_dspy.py
      Agents      : RAG retrieval + MIPROv2-optimised prompts
      Results     : experiments/results/rag_dspy.json

Workflow:
  # Step 1: Run all three conditions (each saves its own results JSON)
  python multi_main.py
  python multi_main_adaptive.py
  python multi_main_rag_dspy.py

  # Step 2: Run comparison across all saved results
  python experiments/run_comparison.py

  # Step 3: Generate thesis tables and charts
  python experiments/analyze_results.py

Components:
  run_comparison.py  — loads all three results JSONs, applies
                       uniform metrics, outputs comparison table
  analyze_results.py — statistical analysis, bar charts, LaTeX
                       table for thesis inclusion
═══════════════════════════════════════════════════════════════════
"""

from experiments.run_comparison  import ComparisonRunner
from experiments.analyze_results import ResultsAnalyzer

__all__ = ["ComparisonRunner", "ResultsAnalyzer"]