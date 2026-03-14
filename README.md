# AI Code Generator – Android Automotive (Local + Validator)

This version adds a **Validator Agent** to simulate CTS/VTS-style checks
for generated VHAL, CarService, and SELinux code.

## Pipeline
Requirement → Code Generation → Validator → Output

## Run
```bash
# 1. Install zstd (required by Ollama)
!apt-get update -y
!apt-get install -y zstd

# 2. Install Ollama
!curl -fsSL https://ollama.com/install.sh | sh

# 3. Start Ollama server
!nohup ollama serve > ollama.log 2>&1 &
!sleep 2
!tail -n 20 ollama.log

ollama pull qwen2.5-coder:32b
ollama list

python3 -m venv .venv
source .venv/bin/activate

pip install --upgrade pip

pip install -r requirements.txt

# ══════════════════════════════════════════════════════════════
# COMPLETE RUN — execute in this exact order, do not skip steps
# ══════════════════════════════════════════════════════════════

# ══════════════════════════════════════════════════════════════
# COMPLETE PROJECT RUN — NOTHING OMITTED
# ══════════════════════════════════════════════════════════════

# ── 1. DEPENDENCIES ─────────────────────────────────────────
pip install chromadb dspy-ai sentence-transformers json5 scipy --break-system-packages

# ── 2. GIT PULL + UPLOAD FIXED FILES ────────────────────────
cd /content/code-codegen-aosp-llm-based
git stash
git pull origin main
git stash pop  # or overwrite with fixed files if conflicts

# Upload/overwrite these fixed files from Claude:
#   agents/vhal_aidl_agent.py
#   agents/vhal_service_agent.py
#   agents/vhal_aidl_build_agent.py
#   agents/vhal_service_build_agent.py
#   agents/selinux_agent.py
#   multi_main_adaptive.py
#   multi_main_rag_dspy.py          (with _score_stage + output_root fixes)
#   apply_chroma_fix.py
#   fix_chroma_singleton.py
#   rescore_all_conditions.py
#   analyze_final.py
#   compare_matched.py
#   verify_and_test.py
#   diagnose_outputs.py
#   run_full_experiment.sh

# ── 3. AOSP SOURCE FOR RAG CORPUS ───────────────────────────
# Skip if aosp_source/ already exists
git clone --depth=1 https://android.googlesource.com/platform/hardware/interfaces aosp_source/hardware
git clone --depth=1 https://android.googlesource.com/platform/system/sepolicy     aosp_source/sepolicy
git clone --depth=1 https://android.googlesource.com/platform/packages/services/Car aosp_source/car

# ── 4. BUILD RAG VECTOR INDEX (~10 min) ─────────────────────
# Skip if rag/chroma_db/ already has data
python -m rag.aosp_indexer --source aosp_source --db rag/chroma_db

# ── 5. CHROMADB SINGLETON FIX ────────────────────────────────
python apply_chroma_fix.py

# ── 6. RUN C1 BASELINE ──────────────────────────────────────
python multi_main.py

# ── 7. RUN C2 ADAPTIVE ──────────────────────────────────────
python multi_main_adaptive.py

# ── 8. DSPy MIPROv2 OPTIMIZER (after C2, before C3) ─────────
python dspy_opt/optimizer.py --mipro-auto light --train-size 2 --force
ls dspy_opt/saved/*/program.json | wc -l   # should be 12

# ── 9. RUN C3 RAG+DSPy ──────────────────────────────────────
python multi_main_rag_dspy.py

# ── 10. VERIFY OUTPUT COMPLETENESS ──────────────────────────
python diagnose_outputs.py

# ── 11. RESCORE ALL THREE CONDITIONS ────────────────────────
python rescore_all_conditions.py

# ── 12. ANALYSIS ────────────────────────────────────────────
python compare_matched.py
python analyze_final.py

# ── 13. VERIFICATION & TESTING (65 test cases) ──────────────
python verify_and_test.py --standalone

# ── 14. AOSP INTEGRATION BUILD ─────────────────────────────
# For each condition dir (output/, output_adaptive/, output_rag_dspy/):
#   Copy HAL files into AOSP tree → mmm build → record pass/fail

# ── 15. APP & BACKEND TESTING ──────────────────────────────
# Kotlin: copy .kt → Android Studio → ./gradlew assembleDebug
# Python: cd backend/vss_dynamic_server/ → pip install -r requirements.txt → python main.py

# ── 16. EXPORT TO LOCAL ────────────────────────────────────
zip -r /content/thesis_export.zip \
    experiments/ output/ output_adaptive/ output_rag_dspy/ \
    dspy_opt/saved/ adaptive_outputs/ \
    -x "*/.llm_draft/*" "*/latest/*"
# from google.colab import files; files.download('/content/thesis_export.zip')

# ── 17. THESIS WRITE-UP ───────────────────────────────────
# Fill [INSERT] placeholders in thesis_chapter4.docx using:
#   experiments/results/final_analysis.md
#   experiments/results/matched_analysis.md
#   experiments/results/latex_table.tex
#   experiments/verification/verification_report.md

# Download output from colab
!!zip -r /content/code-codegen-aosp-llm-based.zip /content/code-codegen-aosp-llm-based/output

# Testing
repo init -u https://android.googlesource.com/platform/manifest -b android-15.0.0_r1
repo sync -j8

# Set your AOSP root
export AOSP_ROOT=/path/to/aosp

# Copy files to AOSP tree
cp -r output/hardware/* $AOSP_ROOT/hardware/
cp -r output/frameworks/* $AOSP_ROOT/frameworks/
cp -r output/packages/* $AOSP_ROOT/packages/
cp output/system/sepolicy/private/*.te $AOSP_ROOT/system/sepolicy/private/

# Add to device manifest
# (See AOSP_BUILDABILITY_ANALYSIS.md for details)

# Build
cd $AOSP_ROOT
m -j$(nproc) android.hardware.automotive.vehicle-service VssDynamicApp

# Build HAL
source build/envsetup.sh
lunch aosp_car_x86_64-userdebug
mmm hardware/interfaces/automotive/vehicle/

# Build App
mmm packages/apps/VssDynamicApp/

# Flash & Run
make -j8
emulator -selinux permissive  # or real AAOS device


```