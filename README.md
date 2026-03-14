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

# 1. Apply ChromaDB fix for C3
python apply_chroma_fix.py

# 2. Re-run ALL THREE pipelines (with 7 fixes + ChromaDB fix applied)
python multi_main.py
python multi_main_adaptive.py
python multi_main_rag_dspy.py

# 3. Rescore all three conditions
python rescore_all_conditions.py

# 4. Run matched-agent comparison (fair subset)
python compare_matched.py

# 5. Run full analysis with statistics
python analyze_final.py

# 6. Run verification and test suite (65 test cases)
python verify_and_test.py --standalone

# 7. AOSP integration build test (on your AOSP source tree)
#    For each condition (output/, output_adaptive/, output_rag_dspy/):
#      cp hardware/interfaces/automotive/vehicle/* $AOSP/vendor/.../vhal/
#      cp sepolicy/**/*.te $AOSP/device/.../sepolicy/vendor/
#      cp packages/apps/VssDynamicApp/* $AOSP/packages/apps/.../
#      source build/envsetup.sh && lunch target-userdebug
#      mmm vendor/.../vhal/impl/

# 8. App and backend testing
#    Kotlin: cp *.kt into Android Studio project → ./gradlew assembleDebug
#    Python: cd backend/vss_dynamic_server/ → pip install -r requirements.txt → python main.py

# 9. Export results to local machine
cd /content/code-codegen-aosp-llm-based
zip -r /content/thesis_export.zip \
    experiments/ output/ output_adaptive/ output_rag_dspy/ \
    -x "*/.llm_draft/*" "*/latest/*"
# Then: from google.colab import files; files.download('/content/thesis_export.zip')

# 10. Fill [INSERT] placeholders in thesis_chapter4.docx with numbers from:
#     experiments/results/final_analysis.md
#     experiments/results/matched_analysis.md
#     experiments/verification/verification_report.md
#     experiments/results/latex_table.tex

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