# RAG Codegen AOSP — LLM-based AAOS HAL Code Generation

LLM-based code generation pipeline for Android Automotive OS (AAOS) Vehicle HAL,
using RAG retrieval, DSPy prompt optimisation, and iterative feedback loops.

Generates complete HAL layer code from VSS (Vehicle Signal Specification) signals:
AIDL interfaces, C++ implementations, SELinux policies, Android.bp build files,
design documents, Android app fragments, and backend servers.

**Target AOSP level: Android 14 (API 34)**
All generated code targets the Android 14 Vehicle HAL API surface. Using a different
AOSP version will cause AIDL interface mismatches and build failures.

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

Use the Colab notebook `RAG_Codegen_aosp_llm_based_full_v7.ipynb` for a guided run.
It handles setup, all 4 conditions, reporting, and Drive backup automatically.

---

## Manual Setup & Run

### 1. Environment Setup

```bash
# System dependencies
apt-get update -y
apt-get install -y clang checkpolicy zstd

# Install Ollama (with parallel inference for faster DSPy)
export OLLAMA_NUM_PARALLEL=4
curl -fsSL https://ollama.com/install.sh | sh
nohup ollama serve > ollama.log 2>&1 &
sleep 5
ollama pull qwen2.5-coder:32b

# Clone this repo
git clone https://github.com/appdev1307/code-codegen-aosp-llm-based.git
cd code-codegen-aosp-llm-based

# Python dependencies (optuna required for MIPROv2 Bayesian search)
pip install -r requirements.txt
pip install chromadb sentence-transformers dspy-ai optuna
pip install pyyaml jinja2 fastapi uvicorn pydantic
```

### 2. Build RAG Index from AOSP Source

ChromaDB **must** be built before DSPy so the optimizer bootstraps traces with RAG context.

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

Execution order matters: C1 → C2 → ChromaDB → DSPy → C3 → C4.

```bash
# ── C1: Baseline ────────────────────────────────────────────
python multi_main.py

# ── C2: Adaptive ────────────────────────────────────────────
python multi_main_adaptive.py

# ── DSPy Optimiser (after ChromaDB, before C3) ──────────────
python dspy_opt/optimizer.py --mipro-auto light --train-size 8 --force
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

Full build validation of generated HAL code against a real Android 14 AOSP source
tree using the Cuttlefish Automotive virtual device.

### Cloud Build with GCP (official AAOS cloud emulator support)

Use a GCP VM with nested virtualization — no local hardware needed.

#### GCP Account & Billing

New Google Cloud customers get **$300 in free credits** (valid 90 days) — more than
enough for the entire AOSP build (~$6 total). No charges unless you manually upgrade.

1. Go to [console.cloud.google.com](https://console.cloud.google.com)
2. Click **"Start Free"** → sign in with your Google account
3. Enter a credit card for identity verification (you won't be charged)
4. $300 credits are available immediately

| Resource | Cost/hr | Time needed | Subtotal |
|----------|---------|-------------|----------|
| c2-standard-32 (repo sync) | ~$1.50 | ~1.5 hrs | ~$2.25 |
| c2-standard-32 (build) | ~$1.50 | ~1.5 hrs | ~$2.25 |
| c2-standard-32 (test) | ~$1.50 | ~1 hr | ~$1.50 |
| **Total** | | | **~$6** |

> **Students:** Check if your university offers Google Cloud for Education credits
> ($50-$100 additional). GitHub Student Developer Pack also includes cloud credits.

#### Create the VM

```bash
# Install gcloud CLI if needed: https://cloud.google.com/sdk/docs/install

# Create VM with nested virtualization for Cuttlefish
gcloud compute instances create aosp-builder \
    --zone=us-central1-a \
    --machine-type=c2-standard-32 \
    --boot-disk-size=500GB \
    --boot-disk-type=pd-ssd \
    --image-family=ubuntu-2204-lts \
    --image-project=ubuntu-os-cloud \
    --enable-nested-virtualization

# SSH in and use screen (survives SSH disconnect)
gcloud compute ssh aosp-builder --zone=us-central1-a
screen -S aosp
```

### Prerequisites

- Linux x86_64 (Ubuntu 22.04 — GCP VM or local)
- 400+ GB free disk, 32+ GB RAM
- ~2 hours for first full build (GCP c2-standard-32)

### Step 1 — Install Build Dependencies

```bash
sudo apt-get install -y git-core gnupg flex bison build-essential \
    zip curl zlib1g-dev libc6-dev-i386 lib32ncurses-dev \
    x11proto-core-dev libx11-dev lib32z1-dev libgl1-mesa-dev \
    libxml2-utils xsltproc unzip fontconfig python3 \
    bridge-utils libvirt-daemon-system
```

### Step 2 — Download AOSP Android 14

**Important:** Use `android-14.0.0_r75`. The generated AIDL interfaces, Vehicle HAL
API surface, and SELinux policy format all target `aosp_level = 14`.
Using Android 13 or 15 will cause build failures.

```bash
# Install repo tool
mkdir -p ~/bin
curl https://storage.googleapis.com/git-repo-downloads/repo > ~/bin/repo
chmod a+x ~/bin/repo
export PATH=~/bin:$PATH

# Create AOSP directory
mkdir ~/aosp-14-auto && cd ~/aosp-14-auto

# Init Android 14 — MUST match pipeline's aosp_level = 14
repo init -u https://android.googlesource.com/platform/manifest \
    -b android-14.0.0_r75 --depth=1

# Sync (~100 GB, 1-2 hours)
repo sync -c -j$(nproc) --no-tags
```

### Step 3 — Build Cuttlefish Automotive Base Image

The `aosp_cf_x86_64_auto` target includes the full AAOS stack:
Car Service, Vehicle HAL framework, AAOS system UI.

```bash
cd ~/aosp-14-auto
source build/envsetup.sh

# MUST use _auto target for automotive
lunch aosp_cf_x86_64_auto-userdebug

# First build (~2-4 hours)
m -j$(nproc)
```

### Step 4 — Copy Files & Apply Android 14 Fixes (automated)

The `apply_aosp14_fixes.sh` script copies generated files to the AOSP tree and
automatically applies all required Android 14 compatibility fixes:

1. Copies AIDL, C++, Android.bp, SELinux, file_contexts to correct AOSP paths
2. Adds `vendor: true` to Android.bp
3. Prepends SELinux type declarations (fixes `checkpolicy` syntax error)
4. Converts HIDL include paths to AIDL (Android 14 format)
5. Fixes AIDL package format and adds `@VintfStability` annotation

```bash
# Upload the script and C4 output to the GCP VM
gcloud compute scp apply_aosp14_fixes.sh aosp-builder:~ --zone=us-central1-a
gcloud compute scp output_c4.zip aosp-builder:~ --zone=us-central1-a

# On the VM:
unzip ~/output_c4.zip -d ~/output_c4_feedback
chmod +x ~/apply_aosp14_fixes.sh
~/apply_aosp14_fixes.sh ~/output_c4_feedback ~/aosp-14-auto
```

The script is idempotent — running it twice won't break anything.
It prints a summary showing how many fixes were applied.

<details>
<summary>Manual fixes (if not using the script)</summary>

```bash
# Fix 1: Android.bp — add vendor: true
# Add to cc_binary block:
#   vendor: true,
#   relative_install_path: "hw",

# Fix 2: SELinux — add type declaration before allow rules
# Prepend to vehicle_hal_adas.te:
#   type hal_vehicle_adas, domain;
#   type hal_vehicle_adas_exec, exec_type, vendor_file_type, file_type;

# Fix 3: C++ — use Android 14 AIDL include paths (NOT HIDL)
#   Correct:  #include <aidl/android/hardware/automotive/vehicle/IVehicle.h>
#   Wrong:    #include <android/hardware/automotive/vehicle/2.0/IVehicle.h>

# Fix 4: AIDL — use Android 14 package format
#   Correct:  package android.hardware.automotive.vehicle;
#   Wrong:    package android.hardware.automotive.vehicle.V2_0;
```
</details>

### Step 5 — Build HAL Module

```bash
cd $AOSP_ROOT
source build/envsetup.sh
lunch aosp_cf_x86_64_auto-userdebug

# Module build first
mmm hardware/interfaces/automotive/vehicle/impl

# Full image if module passes
m -j$(nproc)
```

### Step 6 — Launch Cuttlefish and Test

```bash
# Install Cuttlefish host packages (first time only)
# https://source.android.com/docs/devices/cuttlefish/get-started

# Launch
launch_cvd --daemon

# Connect
adb connect vsock:3:5555
adb wait-for-device

# Test Vehicle HAL
adb shell dumpsys car_service
adb shell cmd car_service list-properties | grep -i adas
adb shell cmd car_service get-property PERF_VEHICLE_SPEED

# Verify SELinux
adb shell getenforce
adb shell dmesg | grep avc

# Run VTS
cd $AOSP_ROOT
atest VtsHalAutomotiveVehicle

# Shutdown
stop_cvd
```

### Step 7 — Test App and Backend

```bash
# Kotlin App (requires AAOS — uses CarPropertyManager API)
# Copy AdasFragment.kt + fragment_adas.xml → Android Studio
# Build: ./gradlew assembleDebug
# Install: adb install app/build/outputs/apk/debug/app-debug.apk

# FastAPI Backend
cd output_c4_feedback/backend/vss_dynamic_server
pip install -r requirements.txt
uvicorn main:app --reload --host 0.0.0.0 --port 8000
curl http://localhost:8000/health
curl http://localhost:8000/properties/list
```

### Step 8 — Clean Up (stop billing)

**Important:** Delete the VM when done to stop charges against your $300 credits.

```bash
# On your local machine (not the VM):
gcloud compute instances delete aosp-builder --zone=us-central1-a

# Verify no VMs are running
gcloud compute instances list
```

You can also stop the VM (without deleting) to pause billing for compute, but
you'll still be charged ~$0.10/day for the 500 GB disk:

```bash
# Stop (keeps disk, pauses compute billing)
gcloud compute instances stop aosp-builder --zone=us-central1-a

# Restart later
gcloud compute instances start aosp-builder --zone=us-central1-a
```

---

## Validation Checklist

| # | Check | Tool | Pass Criteria | Android 14 Notes |
|---|-------|------|---------------|------------------|
| 1 | AIDL syntax | `aidl --lang=java` | Compiles | AIDL package, not HIDL |
| 2 | C++ syntax | `clang++ -fsyntax-only` | No errors | AIDL include paths |
| 3 | SELinux | `checkpolicy -M -c 30` | Compiles | Vendor partition context |
| 4 | Android.bp | Soong (`mmm`) | Builds | `vendor: true` required |
| 5 | VINTF | `assemble_vintf` | Validates | AIDL transport |
| 6 | Kotlin app | `./gradlew assembleDebug` | APK builds | CarPropertyManager API |
| 7 | XML layout | `aapt2 compile` | Compiles | `xmlns:android` auto-injected |
| 8 | Backend | `python -c "import main"` | No errors | — |
| 9 | HAL module | `mmm` | Builds in AOSP 14 | Android 14 tree only |
| 10 | Full image | `m -j$(nproc)` | Builds | `aosp_cf_x86_64_auto` |
| 11 | VTS | `atest VtsHalAutomotiveVehicle` | Passes | Cuttlefish |
| 12 | Runtime | `dumpsys car_service` | Properties visible | Cuttlefish automotive |

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
│   ├── rag_dspy_selinux_agent.py
│   ├── rag_dspy_backend_agent.py
│   └── ...
├── dspy_opt/                      # DSPy optimiser
│   ├── optimizer.py               #   MIPROv2 runner (requires optuna)
│   ├── hal_modules.py             #   Module registry
│   ├── metrics.py                 #   Scoring functions
│   ├── validators.py              #   Syntax validators (clang, checkpolicy, etc.)
│   └── saved/                     #   Optimised programs (12 JSON files)
├── rag/                           # RAG system
│   ├── aosp_indexer.py            #   AOSP → ChromaDB indexer
│   ├── aosp_retriever.py          #   Query retriever
│   └── chroma_db/                 #   Vector database (7 collections, ~29K chunks)
├── dataset/
│   └── vss.json                   # Vehicle Signal Specification (1571 signals)
├── experiments/results/           # Analysis outputs
│   ├── matched_analysis.md        #   4-condition comparison
│   ├── comparison.json
│   ├── latex_table.tex
│   └── final_analysis.md
├── apply_aosp14_fixes.sh          # Automated AOSP 14 integration fixes
├── output/                        # C1 output
├── output_adaptive/               # C2 output
├── output_rag_dspy/               # C3 output
└── output_c4_feedback/            # C4 output (use for AOSP validation)
```

## Known Issues

- **Batch labelling mismatch:** LLM returns 1 signal per batch instead of 4; remaining are padded. This is a prompt/parsing issue in the labelling code — the LLM returns a single JSON object instead of an array.
- **AOSP version:** Generated code targets Android 14 only — do not use Android 13 or 15 source trees. AIDL interfaces and SELinux policy format differ across major versions.

## License

Research use only — MSE thesis project.

