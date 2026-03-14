#!/bin/bash
# run_full_experiment.sh
# ──────────────────────
# End-to-end runner: executes all 3 pipelines, rescores, and generates
# the final analysis.  Run from the project root directory.
#
# Usage:
#   chmod +x run_full_experiment.sh
#   ./run_full_experiment.sh              # run everything
#   ./run_full_experiment.sh --skip-gen   # skip pipeline runs, just rescore+analyze
#
# Prerequisites:
#   - Ollama running with Qwen2.5-Coder:32b at localhost:11434
#   - All 7 fixed agent files in place
#   - Python 3.10+ with required packages

set -euo pipefail

SKIP_GEN=false
if [[ "${1:-}" == "--skip-gen" ]]; then
    SKIP_GEN=true
fi

echo "============================================================"
echo "VHAL Code Generation — Full Experiment Runner"
echo "============================================================"
echo ""

# ── 0. Pre-flight checks ─────────────────────────────────────────
echo "[0/5] Pre-flight checks..."

if ! $SKIP_GEN; then
    if ! curl -s http://localhost:11434/api/tags > /dev/null 2>&1; then
        echo "  ✗ Ollama not reachable at localhost:11434"
        echo "    Start Ollama first: ollama serve"
        exit 1
    fi
    echo "  ✓ Ollama is running"

    MODEL_CHECK=$(curl -s http://localhost:11434/api/tags | python3 -c "
import sys, json
tags = json.load(sys.stdin)
models = [m['name'] for m in tags.get('models', [])]
print('found' if any('qwen2.5-coder' in m for m in models) else 'missing')
" 2>/dev/null || echo "missing")

    if [ "$MODEL_CHECK" != "found" ]; then
        echo "  ✗ Qwen2.5-Coder model not found"
        echo "    Pull it: ollama pull qwen2.5-coder:32b"
        exit 1
    fi
    echo "  ✓ Qwen2.5-Coder model available"
fi

mkdir -p experiments/results
echo "  ✓ Results directory ready"
echo ""

# ── 1. Run pipelines ─────────────────────────────────────────────
if ! $SKIP_GEN; then
    echo "[1/5] Running C1 Baseline pipeline..."
    python multi_main.py 2>&1 | tee logs/c1_run.log || {
        echo "  ✗ C1 pipeline failed — check logs/c1_run.log"
        exit 1
    }
    echo "  ✓ C1 complete"
    echo ""

    echo "[2/5] Running C2 Adaptive pipeline..."
    python multi_main_adaptive.py 2>&1 | tee logs/c2_run.log || {
        echo "  ✗ C2 pipeline failed — check logs/c2_run.log"
        exit 1
    }
    echo "  ✓ C2 complete"
    echo ""

    echo "[3/5] Running C3 RAG+DSPy pipeline..."
    python multi_main_rag_dspy.py 2>&1 | tee logs/c3_run.log || {
        echo "  ✗ C3 pipeline failed — check logs/c3_run.log"
        exit 1
    }
    echo "  ✓ C3 complete"
    echo ""
else
    echo "[1-3/5] Skipping pipeline runs (--skip-gen)"
    echo ""
fi

# ── 2. Rescore all conditions ────────────────────────────────────
echo "[4/5] Rescoring all three conditions..."
mkdir -p logs
python rescore_all_conditions.py 2>&1 | tee logs/rescore.log
echo ""

# ── 3. Final analysis ────────────────────────────────────────────
echo "[5/5] Running final analysis..."
python analyze_final.py 2>&1 | tee logs/analysis.log
echo ""

# ── Summary ───────────────────────────────────────────────────────
echo "============================================================"
echo "EXPERIMENT COMPLETE"
echo "============================================================"
echo ""
echo "Output files:"
echo "  experiments/results/baseline.json     ← C1 scores"
echo "  experiments/results/adaptive.json     ← C2 scores"
echo "  experiments/results/rag_dspy.json     ← C3 scores"
echo "  experiments/results/comparison.json   ← merged comparison"
echo "  experiments/results/final_analysis.md ← full report"
echo "  experiments/results/final_scores.csv  ← flat CSV for R/Excel"
echo "  experiments/results/latex_table.tex   ← LaTeX table"
echo ""
echo "Next steps:"
echo "  1. Review final_analysis.md for results"
echo "  2. Insert scores into thesis_chapter4.docx [INSERT] placeholders"
echo "  3. Import latex_table.tex into your LaTeX thesis"
echo ""
