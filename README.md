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

# Free Account
gcloud compute instances create aosp-builder \
    --zone=us-central1-a \
    --machine-type=n2-standard-8 \
    --boot-disk-size=500GB \
    --boot-disk-type=pd-standard \
    --image-family=ubuntu-2204-lts \
    --image-project=ubuntu-os-cloud \
    --enable-nested-virtualization    

# SSH in and use screen (survives SSH disconnect)
gcloud compute ssh aosp-builder --zone=us-central1-a
screen -S aosp
```

#### Using `screen` (essential for long builds)

The AOSP build takes 2-4 hours. Use `screen` so the build survives if your
browser closes, laptop sleeps, or internet drops.

```bash
# SSH into the VM
gcloud compute ssh aosp-builder --zone=us-central1-a

# Start a named screen session
screen -S aosp

# Now run all build commands inside screen...
# The build keeps running even if you disconnect.
```

**If you get disconnected:**

```bash
# Reconnect to the VM
gcloud compute ssh aosp-builder --zone=us-central1-a

# Reattach to the running build session
screen -d -r aosp
```

**Screen cheat sheet:**

| Action | Keys / Command |
|--------|---------------|
| Detach (leave running) | `Ctrl+A` then `D` |
| Reattach | `screen -r aosp` |
| List sessions | `screen -ls` |
| Scroll up | `Ctrl+A` then `Esc`, then arrow keys |
| Exit scroll | `Esc` |

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

# Configure git (change email/name if you want)
git config --global user.name "Nguyen Ngoc Tam"
git config --global user.email "nguyenngoctam1307@gmail.com"

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
lunch aosp_cf_x86_64_auto-trunk_staging-userdebug

cd ~/aosp-14-auto/platform_testing

# Replace the whole Automotive block with a clean one
cat > build/tasks/tests/native_test_list.mk.tmp << EOF
ifeq (\$(BOARD_IS_AUTOMOTIVE), true)
native_tests += \\
    libwatchdog_test \\
    evsmanagerd_test
endif
EOF

sed -i '/ifeq (\$(BOARD_IS_AUTOMOTIVE)/,/endif/d' build/tasks/tests/native_test_list.mk
cat build/tasks/tests/native_test_list.mk.tmp >> build/tasks/tests/native_test_list.mk
rm build/tasks/tests/native_test_list.mk.tmp

cd ..

# First build (~2-4 hours)
m -j$(nproc)


# Add yourself to the disk group
sudo usermod -aG disk $USER

# Reboot the VM (important for group change to take effect)
sudo reboot

gcloud compute instances list --filter="name=aosp-builder"

gcloud compute ssh aosp-builder --zone=us-central1-a

cd ~/aosp-14-auto
. build/envsetup.sh
lunch aosp_cf_x86_64_auto-trunk_staging-userdebug

m clean-openwrt_rootfs_customization_x86_64
m -j$(nproc)

```

### Step 4 — Transfer outputs to GCP VM

The fix script comes directly from GitHub. Choose one method for the output zips:

**Option A: Upload via GCS bucket in browser (simplest — no CLI needed)**

1. Go to [console.cloud.google.com/storage](https://console.cloud.google.com/storage)
2. Click **Create Bucket** → name it `aosp-thesis-temp` → Create
3. Click the bucket → **Upload Files** → select `output_c1.zip`, `output_c2.zip`, `output_c3.zip`, `output_c4.zip`
4. On the GCP VM:

```bash
# Download from GCS bucket
gsutil cp gs://aosp-thesis-temp/*.zip ~/

# Clean up bucket when done (avoid storage charges)
# gsutil rm -r gs://aosp-thesis-temp
```

**Option B: SCP from local terminal**

```bash
# From your local terminal (adjust path to where you saved them)
gcloud compute scp ~/Downloads/output_c1.zip aosp-builder:~ --zone=us-central1-a
gcloud compute scp ~/Downloads/output_c2.zip aosp-builder:~ --zone=us-central1-a
gcloud compute scp ~/Downloads/output_c3.zip aosp-builder:~ --zone=us-central1-a
gcloud compute scp ~/Downloads/output_c4.zip aosp-builder:~ --zone=us-central1-a
```

**Then on the GCP VM (both options):**

```bash
# Get fix script from GitHub
curl -o ~/apply_aosp14_fixes.sh \
    https://raw.githubusercontent.com/appdev1307/code-codegen-aosp-llm-based/main/apply_aosp14_fixes.sh
chmod +x ~/apply_aosp14_fixes.sh

# Unzip all conditions
unzip ~/output_c1.zip -d ~/output_c1
unzip ~/output_c2.zip -d ~/output_c2
unzip ~/output_c3.zip -d ~/output_c3
unzip ~/output_c4.zip -d ~/output_c4
```

### Step 5 — Apply Fixes & Build

```bash
cd ~/aosp-14-auto
source build/envsetup.sh
lunch aosp_cf_x86_64_auto-trunk_staging-userdebug


# Helper: clean previous condition's files
clean_hal() {
    rm -f hardware/interfaces/automotive/vehicle/aidl/android/hardware/automotive/vehicle/VehicleProperty*.aidl
    rm -f hardware/interfaces/automotive/vehicle/impl/VehicleHalService*.cpp
    rm -f hardware/interfaces/automotive/vehicle/impl/Android.bp.generated
    rm -f system/sepolicy/vendor/vehicle_hal_*.te
}
```

**Option A: Build a single condition**

```bash
# Set condition: c1 (default), c2, c3, or c4
COND1=c1

clean_hal
~/apply_aosp14_fixes.sh ~/output_$COND1 ~/aosp-14-auto
mmm hardware/interfaces/automotive/vehicle/impl 2>&1 | tee ~/build_${COND1}.log
echo "Result: $COND1 → exit code $?"
```

**Option B: Build all 4 conditions for thesis comparison**

```bash
for COND1 in c1 c2 c3 c4; do
    echo "═══════════════════════════════════════════"
    echo "  Building condition: $COND1"
    echo "═══════════════════════════════════════════"
    clean_hal
    ~/apply_aosp14_fixes.sh ~/output_$COND1 ~/aosp-14-auto
    mmm hardware/interfaces/automotive/vehicle/impl 2>&1 | tee ~/build_${COND1}.log
    echo "  Result: $COND1 → exit code $?"
    echo ""
done

echo "Build logs: ~/build_c1.log ~/build_c2.log ~/build_c3.log ~/build_c4.log"
```

### Step 6 — Full AOSP Image Build

Build the full AOSP image with your chosen condition:

```bash
# Set condition: c4 (default — highest scoring), c1, c2, or c3
COND2=c4

clean_hal
~/apply_aosp14_fixes.sh ~/output_$COND2 ~/aosp-14-auto
m -j$(nproc) 2>&1 | tee ~/build_full_${COND2}.log
```

<details>
<summary>Manual fixes (if apply_aosp14_fixes.sh doesn't cover an edge case)</summary>

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

### Step 7 — Launch Cuttlefish and Test

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

### Step 8 — Test App and Backend

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

### Step 9 — Clean Up (stop billing)

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

## Validation Metrics

### Two-tier validation approach

The pipeline uses two complementary validation methods:

1. **Colab validation** — automated synthetic scoring during generation (structure + syntax + coverage)
2. **AOSP build validation** — ground truth compilation in a real Android 14 source tree

Comparing both reveals validator blind spots (scores high in Colab but fails AOSP build)
and validator strictness (scores low in Colab but passes AOSP build).

### Build validation scoring

| Result | Score | Meaning |
|--------|-------|---------|
| Compiles without changes | 1.0 | Production-ready |
| Compiles after automated fix (`apply_aosp14_fixes.sh`) | 0.8 | Needs predictable patches |
| Compiles after manual fix | 0.5 | Structural issues |
| Does not compile | 0.0 | Fundamental errors |

### Validation checklist (core AOSP artifacts)

| # | Artifact | Colab Tool | AOSP Build Tool | Auto-fix applied |
|---|----------|------------|-----------------|------------------|
| 1 | AIDL | Python AIDL parser | `aidl --lang=java` | Package format (V2_0 → flat) |
| 2 | C++ | `clang++ -fsyntax-only` | `mmm` (Soong/clang) | HIDL → AIDL include paths |
| 3 | SELinux | `checkpolicy -M -c 30` | Full policy compile | Type declaration prepended |
| 4 | Android.bp | Python BP parser | `mmm` (Soong) | `vendor: true` injected |

### Validation checklist (supporting artifacts)

| # | Artifact | Tool | Pass Criteria |
|---|----------|------|---------------|
| 5 | VINTF manifest | `assemble_vintf` | Schema validates |
| 6 | Kotlin app | `./gradlew assembleDebug` | APK builds |
| 7 | XML layout | `aapt2 compile` | Resources compile |
| 8 | Backend | `python -c "import main"` | No import errors |
| 9 | Full image | `m -j$(nproc)` | `aosp_cf_x86_64_auto` builds |
| 10 | VTS | `atest VtsHalAutomotiveVehicle` | Tests pass on Cuttlefish |

### AOSP build validation script

Run on the GCP VM after `apply_aosp14_fixes.sh` to generate a JSON report:

```bash
#!/bin/bash
# validate_aosp_build.sh — produces build_validation_report.json

AOSP_ROOT=~/aosp-14-auto
REPORT="$HOME/build_validation_report.json"

cd $AOSP_ROOT
source build/envsetup.sh
lunch aosp_cf_x86_64_auto-trunk_staging-userdebug

echo '{"artifacts": [' > $REPORT

# 1. AIDL
AIDL_FILE=$(find hardware/interfaces/automotive/vehicle/aidl -name "*.aidl" | head -1)
aidl --lang=java "$AIDL_FILE" 2>/tmp/aidl_err; AIDL_RC=$?
echo "  {\"artifact\":\"aidl\",\"rc\":$AIDL_RC,\"errors\":$(grep -c error /tmp/aidl_err)}," >> $REPORT

# 2. C++
CPP_FILE=$(find hardware/interfaces/automotive/vehicle/impl -name "*.cpp" | head -1)
clang++ -fsyntax-only -std=c++17 "$CPP_FILE" 2>/tmp/cpp_err; CPP_RC=$?
echo "  {\"artifact\":\"cpp\",\"rc\":$CPP_RC,\"errors\":$(grep -c error /tmp/cpp_err)}," >> $REPORT

# 3. SELinux
SE_FILE=$(find system/sepolicy/vendor -name "*.te" | head -1)
checkpolicy -M -c 30 -o /dev/null "$SE_FILE" 2>/tmp/se_err; SE_RC=$?
echo "  {\"artifact\":\"selinux\",\"rc\":$SE_RC}," >> $REPORT

# 4. Module build (AIDL + C++ + Android.bp together)
mmm hardware/interfaces/automotive/vehicle/impl 2>/tmp/mmm_err; MMM_RC=$?
echo "  {\"artifact\":\"module_build\",\"rc\":$MMM_RC,\"errors\":$(grep -c 'FAILED\|error:' /tmp/mmm_err)}," >> $REPORT

# 5. Full AOSP build
m -j$(nproc) 2>&1 | tail -5 > /tmp/full_err; FULL_RC=$?
echo "  {\"artifact\":\"full_build\",\"rc\":$FULL_RC}" >> $REPORT

echo ']}' >> $REPORT
echo "Report: $REPORT"
cat $REPORT
```

Download the report for thesis analysis:

```bash
gcloud compute scp aosp-builder:~/build_validation_report.json . --zone=us-central1-a
```

### Thesis results table format

Present both metrics side by side in your thesis:

| Artifact | Colab Score | AOSP Build | Auto-fix | Final Build Score |
|----------|------------|------------|----------|-------------------|
| AIDL | 1.000 | ✓ / ✗ | package format | 1.0 / 0.8 / 0.0 |
| C++ | 0.878 | ✓ / ✗ | include paths | 1.0 / 0.8 / 0.0 |
| SELinux | 0.747 | ✓ / ✗ | type declaration | 1.0 / 0.8 / 0.0 |
| Android.bp | 0.710 | ✓ / ✗ | vendor: true | 1.0 / 0.8 / 0.0 |
| Module build | — | ✓ / ✗ | — | 1.0 / 0.0 |
| Full image | — | ✓ / ✗ | — | 1.0 / 0.0 |

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
├── validate_aosp_build.sh         # AOSP build validation → JSON report
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

