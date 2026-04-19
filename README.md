# RAG Codegen AOSP — LLM-based AAOS HAL Code Generation

LLM-based code generation pipeline for Android Automotive OS (AAOS) Vehicle HAL,
using RAG retrieval, DSPy prompt optimisation, and iterative feedback loops.

Generates complete HAL layer code from VSS (Vehicle Signal Specification) signals:
AIDL interfaces, C++ implementations, SELinux policies, Android.bp build files,
design documents, Android app fragments, and backend servers.

## Architecture

```
VSS Signals → Labelling → YAML Spec → Module Planner → Code Generation → Validation → Output
                                                              ↑
                                          RAG (ChromaDB) + DSPy (MIPROv2)
```

## Experimental Conditions

| Condition | Script | Description | Avg Score |
|-----------|--------|-------------|-----------|
| C1 Baseline | `multi_main.py` | Vanilla LLM generation | 0.827 |
| C2 Adaptive | `multi_main_adaptive.py` | RL-based prompt selection | 0.825 |
| C3 RAG+DSPy | `multi_main_rag_dspy.py` | RAG context + optimised prompts | 0.878 |
| C4 Feedback | `multi_main_c4_feedback.py` | C3 + generate→validate→refine loop | 0.909 |

## Requirements

- **Runtime:** Google Colab A100 High-RAM (recommended) or Linux with NVIDIA GPU (32GB+ VRAM)
- **Model:** Qwen 2.5-coder:32b via Ollama
- **Disk:** ~50 GB (model + AOSP source + ChromaDB)
- **Python:** 3.10+

---

## Quick Start (Colab)

Use the Colab notebook `RAG_Codegen_aosp_llm_based_full_v6.ipynb` for a guided run.
It handles setup, all 4 conditions, reporting, and Drive backup automatically.

---

## Manual Setup & Run

### 1. Environment Setup

```bash
# System dependencies
apt-get update -y
apt-get install -y clang checkpolicy zstd

# Install Ollama
curl -fsSL https://ollama.com/install.sh | sh
nohup ollama serve > ollama.log 2>&1 &
sleep 5
ollama pull qwen2.5-coder:32b

# Clone this repo
git clone https://github.com/appdev1307/code-codegen-aosp-llm-based.git
cd code-codegen-aosp-llm-based

# Python dependencies
pip install -r requirements.txt
pip install chromadb sentence-transformers dspy-ai
pip install pyyaml jinja2 fastapi uvicorn pydantic
```

### 2. Build RAG Index from AOSP Source

```bash
# Shallow-clone AOSP repos (~300 MB total)
git clone --depth=1 https://android.googlesource.com/platform/hardware/interfaces aosp_source/hardware
git clone --depth=1 https://android.googlesource.com/platform/system/sepolicy     aosp_source/sepolicy
git clone --depth=1 https://android.googlesource.com/platform/packages/services/Car aosp_source/car

# Index into ChromaDB (~2 min on GPU)
python -m rag.aosp_indexer --source aosp_source --db rag/chroma_db
# Expected: 7 collections, ~29,119 chunks
```

### 3. Run All Conditions

```bash
# ── C1: Baseline ────────────────────────────────────────────
python multi_main.py

# ── C2: Adaptive ────────────────────────────────────────────
python multi_main_adaptive.py

# ── DSPy Optimiser (after C2, before C3) ────────────────────
python dspy_opt/optimizer.py --mipro-auto light --train-size 2 --force
ls dspy_opt/saved/*/program.json | wc -l   # expect: 12

# ── ChromaDB fix (before C3) ───────────────────────────────
python apply_chroma_fix.py

# ── C3: RAG + DSPy ─────────────────────────────────────────
python multi_main_rag_dspy.py

# ── C4: Feedback Loop ──────────────────────────────────────
python multi_main_c4_feedback.py
```

### 4. Analysis & Reporting

```bash
python diagnose_outputs.py
python rescore_all_conditions.py
python compare_matched.py
python analyze_final.py

# View results
cat experiments/results/matched_analysis.md
```

### 5. Export

```bash
zip -r thesis_export.zip \
    experiments/ output/ output_adaptive/ output_rag_dspy/ output_c4_feedback/ \
    dspy_opt/saved/ \
    -x "*/.llm_draft/*" "*/latest/*"
```

---

## AOSP Source Tree Validation

Full build validation of generated HAL code against a real AOSP source tree
using the Cuttlefish Automotive virtual device.

### Prerequisites

- Linux x86_64 (Ubuntu 20.04 or 22.04)
- 400+ GB free disk space
- 32+ GB RAM (64 GB recommended)
- ~4 hours for first full build

### Step 1 — Download AOSP Android 14

```bash
# Install repo tool
mkdir -p ~/bin
curl https://storage.googleapis.com/git-repo-downloads/repo > ~/bin/repo
chmod a+x ~/bin/repo
export PATH=~/bin:$PATH

# Create AOSP directory
mkdir ~/aosp-14-auto && cd ~/aosp-14-auto

# Init Android 14 (matches pipeline's aosp_level = 14)
repo init -u https://android.googlesource.com/platform/manifest \
    -b android-14.0.0_r75 --depth=1

# Sync (~100 GB, 1-2 hours)
repo sync -c -j$(nproc) --no-tags
```

### Step 2 — Build Cuttlefish Automotive Base Image

```bash
cd ~/aosp-14-auto
source build/envsetup.sh
lunch aosp_cf_x86_64_auto-userdebug
m -j$(nproc)
```

This builds a complete AAOS image with Vehicle HAL that runs without hardware.

### Step 3 — Copy Generated HAL Files

Use C4 output (highest scoring condition):

```bash
export AOSP_ROOT=~/aosp-14-auto
export C4_OUT=/path/to/output_c4_feedback

# ── AIDL Interface ──────────────────────────────────────────
cp $C4_OUT/hardware/interfaces/automotive/vehicle/aidl/android/hardware/automotive/vehicle/VehiclePropertyAdas.aidl \
   $AOSP_ROOT/hardware/interfaces/automotive/vehicle/aidl/android/hardware/automotive/vehicle/

# ── C++ Implementation ─────────────────────────────────────
cp $C4_OUT/hardware/interfaces/automotive/vehicle/impl/VehicleHalServiceAdas.cpp \
   $AOSP_ROOT/hardware/interfaces/automotive/vehicle/impl/

# ── Build Files ─────────────────────────────────────────────
# Copy generated Android.bp (merge with existing, don't overwrite)
cp $C4_OUT/hardware/interfaces/automotive/vehicle/impl/Android.bp \
   $AOSP_ROOT/hardware/interfaces/automotive/vehicle/impl/Android.bp.generated

# ── SELinux Policy ──────────────────────────────────────────
cp $C4_OUT/sepolicy/vehicle_hal_adas.te \
   $AOSP_ROOT/system/sepolicy/vendor/

# ── VINTF Manifest + Init ──────────────────────────────────
# Copy if generated by BuildGlue:
cp $C4_OUT/private/file_contexts $AOSP_ROOT/system/sepolicy/vendor/ 2>/dev/null
```

### Step 4 — Apply Manual Fixes

Three known issues from validation scoring need manual correction:

```bash
# ── Fix 1: Android.bp — add vendor: true ────────────────────
# Open the generated Android.bp and add to cc_binary block:
#   vendor: true,
#   relative_install_path: "hw",

# ── Fix 2: SELinux — add type declaration ────────────────────
# The .te file starts with allow rules without declaring the type.
# Prepend this line to vehicle_hal_adas.te:
#   type hal_vehicle_adas, domain;
#   type hal_vehicle_adas_exec, exec_type, vendor_file_type, file_type;

# ── Fix 3: C++ headers — verify AOSP include paths ──────────
# Ensure includes match the AOSP tree structure:
#   #include <android/hardware/automotive/vehicle/2.0/IVehicle.h>
#   or for AIDL:
#   #include <aidl/android/hardware/automotive/vehicle/IVehicle.h>
```

### Step 5 — Build the HAL Module

```bash
cd $AOSP_ROOT
source build/envsetup.sh
lunch aosp_cf_x86_64_auto-userdebug

# Build just the vehicle HAL module
mmm hardware/interfaces/automotive/vehicle/impl

# If successful, build full image
m -j$(nproc)
```

### Step 6 — Launch Cuttlefish and Test

```bash
# Install Cuttlefish host tools (first time only)
sudo apt install -y bridge-utils libvirt-daemon-system
# Follow: https://source.android.com/docs/devices/cuttlefish/get-started

# Launch virtual automotive device
launch_cvd --daemon

# Connect via WebUI at https://localhost:8443
# Or via adb:
adb connect vsock:3:5555

# ── Test Vehicle HAL Properties ─────────────────────────────
adb shell dumpsys car_service
adb shell cmd car_service list-properties | grep -i adas
adb shell cmd car_service get-property PERF_VEHICLE_SPEED

# ── Verify SELinux ──────────────────────────────────────────
adb shell getenforce          # Should be "Enforcing"
adb shell dmesg | grep avc    # Check for denials

# ── Run VTS Tests (if available) ────────────────────────────
atest VtsHalAutomotiveVehicle
```

### Step 7 — Test Android App and Backend

```bash
# ── Kotlin App ──────────────────────────────────────────────
# Copy .kt and .xml files to Android Studio project
# Build: ./gradlew assembleDebug
# Install: adb install app/build/outputs/apk/debug/app-debug.apk

# ── FastAPI Backend ─────────────────────────────────────────
cd output_c4_feedback/backend/vss_dynamic_server
pip install -r requirements.txt
uvicorn main:app --reload --host 0.0.0.0 --port 8000
# Test: curl http://localhost:8000/health
# Test: curl http://localhost:8000/api/data
```

---

## Validation Checklist

| Check | Tool | Pass Criteria |
|-------|------|---------------|
| AIDL syntax | `aidl --lang=java` | Compiles without errors |
| C++ syntax | `clang++ --syntax-only` | No syntax errors |
| SELinux policy | `checkpolicy -M -c 30` | Policy compiles |
| Android.bp | `androidmk` / Soong | Module builds |
| VINTF manifest | `assemble_vintf` | Schema validates |
| Kotlin app | `./gradlew assembleDebug` | APK builds |
| XML layout | `aapt2 compile` | Resources compile |
| Python backend | `python -c "import main"` | No import errors |
| Full AOSP build | `m -j$(nproc)` | Image builds |
| VTS tests | `atest VtsHalAutomotiveVehicle` | Tests pass |

---

## Project Structure

```
code-codegen-aosp-llm-based/
├── multi_main.py                  # C1: Baseline pipeline
├── multi_main_adaptive.py         # C2: Adaptive pipeline
├── multi_main_rag_dspy.py         # C3: RAG+DSPy pipeline
├── multi_main_c4_feedback.py      # C4: Feedback loop pipeline
├── agents/                        # Generation agents
│   ├── rag_dspy_mixin.py          #   RAG+DSPy shared logic
│   ├── rag_dspy_architect_agent.py
│   ├── rag_dspy_aidl_agent.py
│   ├── rag_dspy_cpp_agent.py
│   ├── rag_dspy_backend_agent.py
│   └── ...
├── dspy_opt/                      # DSPy optimiser
│   ├── optimizer.py               #   MIPROv2 runner
│   ├── hal_modules.py             #   Module registry
│   ├── metrics.py                 #   Scoring functions
│   ├── validators.py              #   Syntax validators
│   └── saved/                     #   Optimised programs (12 JSON files)
├── rag/                           # RAG system
│   ├── aosp_indexer.py            #   AOSP → ChromaDB indexer
│   ├── aosp_retriever.py          #   Query retriever
│   └── chroma_db/                 #   Vector database (7 collections)
├── dataset/
│   └── vss.json                   # Vehicle Signal Specification
├── experiments/results/           # Analysis outputs
│   ├── matched_analysis.md        #   4-condition comparison
│   ├── comparison.json
│   ├── latex_table.tex
│   └── final_analysis.md
├── output/                        # C1 output
├── output_adaptive/               # C2 output
├── output_rag_dspy/               # C3 output
└── output_c4_feedback/            # C4 output (use for AOSP validation)
```

## Known Issues

- **Batch labelling mismatch:** LLM returns 1 signal per batch instead of 4; remaining are padded
- **SELinux in C4:** Missing `service_name` input field causes score=0.300 in feedback loop
- **C++ clang validation:** `--syntax-only` flag not supported on some clang versions; code quality is higher than score suggests
- **android_layout XML:** Occasional missing namespace declaration (`xmlns:android`)

## License

Research use only — MSE thesis project.
