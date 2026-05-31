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
C1-C4: VSS Signals → Labelling → YAML Spec → Module Planner → Code Generation → Validation
                                                                      ↑
                                                  RAG (ChromaDB) + DSPy (MIPROv2)

C5: Advanced Runtime Validation (builds on C4 output + compiled AOSP tree)
    ┌─────────────────────────────────────────────────────────────┐
    │  Input: C4 YAML Spec + MODULE_PLAN + AOSP Build Dump        │
    │         + FakeVehicleHardware.cpp (from GCP VM)             │
    ├─────────────────────────────────────────────────────────────┤
    │  Agent 1: FakeVehicleHardware Patcher (RAG+DSPy+Feedback)   │
    │    → Extends FakeVehicleHardware.cpp to serve VSS at runtime │
    │  Agent 2: VTS Generator (RAG+DSPy+Feedback)                 │
    │    → Custom VtsHalAutomotiveVehicleVss.cpp for VSS tests     │
    │  Agent 3: HMI App Generator (C4 DSPy programs reused)       │
    │    → Kotlin fragments + XML layouts with real property IDs   │
    ├─────────────────────────────────────────────────────────────┤
    │  Output: Cuttlefish runtime integration                      │
    │    → VSS properties served by FakeVehicleHardware            │
    │    → VTS tests pass on Cuttlefish                            │
    │    → HMI app interacts with real VSS signals                 │
    └─────────────────────────────────────────────────────────────┘
```

## Experimental Conditions

| Condition | Script | Description | Matched Avg | Full Avg |
|-----------|--------|-------------|-------------|----------|
| C1 Baseline | `multi_main.py` | Vanilla LLM generation | 0.817 | 0.817 |
| C2 Adaptive | `multi_main_adaptive.py` | Thompson Sampling prompt selection | 0.803 | 0.806 |
| C3 RAG+DSPy | `multi_main_rag_dspy.py` | RAG context + DSPy optimised prompts | 0.858 | 0.860 |
| C4 Feedback | `multi_main_c4_feedback.py` | C3 + post-validation retry loop | **0.876** | **0.878** |
| C5 Full | `multi_main_c5.py` | Advanced runtime validation — FakeVHAL patch + VTS + HMI app | — | — |

C2 and C3 are independent enhancements over C1; C4 combines both with a validation feedback loop;
C5 is the advanced runtime validation layer that takes C4 output and proves end-to-end integration
on a real AAOS Cuttlefish instance:

```
        C1 (baseline LLM)
       /                \
  C2 (+ Thompson)    C3 (+ RAG + DSPy)
       \                /
        C4 (both + feedback loop)
               ↓
        C5 (runtime validation on Cuttlefish)
          ├── Agent 1: FakeVehicleHardware patch (RAG+DSPy+feedback)
          ├── Agent 2: VTS test generation (RAG+DSPy+feedback)
          └── Agent 3: HMI app (C4 DSPy programs reused)
```

**What C5 reuses from C1-C4:**
- All 12 optimised DSPy programs from `dspy_opt/saved/`
- RAG retriever + ChromaDB (same 7 collections, 17.6K chunks)
- `rag_dspy_mixin.py` base class for all agents
- `llm_client.py` Ollama wrapper
- C4 feedback retry pattern (validate → error feedback → retry)
- `output_c4_feedback/SPEC_FROM_VSS_*.yaml` and `MODULE_PLAN.json`

**What C5 adds new:**
- `FakeVehicleHardwarePatchAgent` — extends FakeVehicleHardware.cpp to serve VSS properties at runtime
- `VtsGeneratorAgent` — custom VTS tests (`VtsHalAutomotiveVehicleVss.cpp`) for VSS properties
- `mmm` AOSP build as runtime validator (not just `clang++`)
- AOSP source tree + compiled dump as primary inputs

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

The indexer automatically **excludes HIDL files** (vehicle/2.0/, V2_0 namespaces) and only
indexes AIDL-compatible code. This is critical — without this filter, the RAG corpus
contains both HIDL and AIDL examples, and the LLM generates legacy HIDL includes
(`hidl/Status.h`, `vehicle/2.0/IVehicle.h`) that don't compile in Android 14's AIDL-based tree.

**Important:** Clone the same AOSP tag (`android-14.0.0_r75`) used for the build tree.
Mismatched versions cause the LLM to generate patterns that don't match the build system.

```bash
# Shallow-clone AOSP repos pinned to android-14.0.0_r75 (~300 MB total)
git clone --depth=1 -b android-14.0.0_r75 \
    https://android.googlesource.com/platform/hardware/interfaces aosp_source/hardware
git clone --depth=1 -b android-14.0.0_r75 \
    https://android.googlesource.com/platform/system/sepolicy     aosp_source/sepolicy
git clone --depth=1 -b android-14.0.0_r75 \
    https://android.googlesource.com/platform/packages/services/Car aosp_source/car

# Index into ChromaDB with AIDL-only filter (~2 min on GPU)
python -m rag.aosp_indexer --source aosp_source --db rag/chroma_db --force
# Expected: 7 collections, AIDL-only (no HIDL/V2_0 content)
```

### 3. Run All Conditions

Execution order matters: C1 → C2 → ChromaDB → DSPy → C3 → C4 → C5.

```bash
# ── C1: Baseline ────────────────────────────────────────────
python multi_main.py
cp -r output/ output_c1_backup/

# ── C2: Adaptive ────────────────────────────────────────────
python multi_main_adaptive.py
cp -r output_adaptive/ output_adaptive_backup/

# ── ChromaDB: Build RAG corpus (before DSPy) ────────────────
# Clone AOSP source pinned to android-14.0.0_r75
mkdir -p aosp_source
git clone --depth=1 -b android-14.0.0_r75 \
  https://android.googlesource.com/platform/hardware/interfaces aosp_source/hardware
git clone --depth=1 -b android-14.0.0_r75 \
  https://android.googlesource.com/platform/system/sepolicy aosp_source/sepolicy
git clone --depth=1 -b android-14.0.0_r75 \
  https://android.googlesource.com/platform/packages/services/Car aosp_source/car

# Index AOSP → ChromaDB (AIDL-only, HIDL excluded)
python -m rag.aosp_indexer --source aosp_source --db rag/chroma_db --force

# Verify
python -c "import chromadb; c=chromadb.PersistentClient('rag/chroma_db'); print(sum(col.count() for col in c.list_collections()), 'chunks')"

# ── DSPy Optimiser (after ChromaDB, before C3) ──────────────
python dspy_opt/optimizer.py --mipro-auto light --train-size 8 --force
ls dspy_opt/saved/*/program.json | wc -l   # expect: 12

# ── ChromaDB fix (before C3) ────────────────────────────────
python apply_chroma_fix.py

# ── C3: RAG + DSPy ─────────────────────────────────────────
python multi_main_rag_dspy.py
cp -r output_rag_dspy/ output_rag_dspy_backup/

# ── C4: Feedback Loop ──────────────────────────────────────
python multi_main_c4_feedback.py
cp -r output_c4_feedback/ output_c4_feedback_backup/

# ── C5: Advanced Runtime Validation ────────────────────────
# Step 1: Authenticate GCS (Colab only)
# from google.colab import auth; auth.authenticate_user()

# Step 2: Copy FakeVehicleHardware from GCP VM
# (aosp_source/ already exists from ChromaDB RAG step above)
gsutil cp gs://aosp-thesis-temp/FakeVehicleHardware.cpp aosp_source/

# Step 3: Download compiled AIDL property IDs
mkdir -p aosp_dump
gsutil cp gs://aosp-thesis-temp/aosp_dump.zip .
unzip aosp_dump.zip -d aosp_dump_raw
cp aosp_dump_raw/VehicleProperty*.aidl aosp_dump/
# Note: -j flag in zip stores filenames only (no subdirs), so no wildcard needed

# Step 4: Run C5
python multi_main_c5.py

# Step 5: Upload to GCP VM
zip -r output_c5.zip output_c5/
gsutil cp output_c5.zip gs://aosp-thesis-temp/
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
    experiments/ output/ output_adaptive/ output_rag_dspy/ output_c4_feedback/ output_c5/ \
    dspy_opt/saved/ \
    -x "*/.llm_draft/*" "*/latest/*"
```

---

## C5 — Advanced Runtime Validation Pipeline

C5 is the advanced validation layer that proves generated VSS artifacts work at runtime
on a real AAOS Cuttlefish instance. It builds directly on C4 output and the AOSP build tree.

### C5 vs C1-C4

| Aspect | C1-C4 | C5 |
|--------|-------|-----|
| Input | VSS signals (1571 leaf signals) | C4 output + AOSP build tree |
| Validation | Compile-time (clang++, checkpolicy) | Runtime (mmm + atest on Cuttlefish) |
| Output | AIDL, C++, SELinux, Android.bp | FakeVHAL patch + VTS tests + HMI app |
| VHAL serving | Not served at runtime | FakeVehicleHardware extended to serve VSS |
| App | Template/LLM fragments (no real IDs) | Real CarPropertyManager IDs from AOSP build |

### C5 Prerequisites on GCP VM

**First — grant GCS write access to the VM service account (run in Cloud Shell, not VM):**

```bash
gcloud storage buckets add-iam-policy-binding gs://aosp-thesis-temp \
  --member="serviceAccount:751513866050-compute@developer.gserviceaccount.com" \
  --role="roles/storage.objectAdmin"
```

**Then on the GCP VM:**

```bash
# Copy frm Mac
gcloud compute scp /Users/macintoshhd/Downloads/VssProperties.json nguyenngoctam1307@aosp-builder-cutterfish:~/aosp-14-auto/ --zone=us-central1-a

BUCKET=gs://aosp-thesis-temp
cd ~/aosp-14-auto

# 1. FakeVehicleHardware — restore from git first (may have been patched)
git -C hardware/interfaces checkout \
  automotive/vehicle/aidl/impl/fake_impl/hardware/src/FakeVehicleHardware.cpp

# 2. FakeVehicleHardware source
gsutil cp \
  hardware/interfaces/automotive/vehicle/aidl/impl/fake_impl/hardware/src/FakeVehicleHardware.cpp \
  $BUCKET/FakeVehicleHardware.cpp

# 3. AOSP compiled AIDL property ID dump (-j stores filenames only, no directory path)
DUMP_DIR=out/soong/.intermediates/hardware/interfaces/automotive/vehicle/aidl/android.hardware.automotive.vehicle-api/dump/android/hardware/automotive/vehicle
zip -j ~/aosp_dump.zip $DUMP_DIR/VehicleProperty*.aidl
gsutil cp ~/aosp_dump.zip $BUCKET/aosp_dump.zip

# Verify both uploaded
gsutil ls $BUCKET/
# Expected:
#   gs://aosp-thesis-temp/FakeVehicleHardware.cpp
#   gs://aosp-thesis-temp/aosp_dump.zip
```

### C5 Run on Colab

```python
# Authenticate GCS
from google.colab import auth
auth.authenticate_user()
```

```bash
# Copy FakeVehicleHardware from GCS
# (aosp_source/ already exists from ChromaDB RAG step)
gsutil cp gs://aosp-thesis-temp/FakeVehicleHardware.cpp aosp_source/

# Download compiled AIDL property IDs
mkdir -p aosp_dump
gsutil cp gs://aosp-thesis-temp/aosp_dump.zip .
unzip aosp_dump.zip -d aosp_dump_raw
cp aosp_dump_raw/VehicleProperty*.aidl aosp_dump/
# Note: -j flag in zip means files are at root of zip, not in subdirectory

gsutil rm gs://aosp-thesis-temp/output_c5.zip

# Run C5 (reads C4 output + AOSP assets automatically)
python multi_main_c5.py
```

### C5 Deploy on GCP VM - In later chapters.

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


#### Create the VM
```bash
gcloud compute instances create aosp-builder-cutterfish \
  --zone=us-central1-a \
  --machine-type=n2-standard-16 \
  --boot-disk-size=500GB \
  --image-family=ubuntu-2204-lts \
  --image-project=ubuntu-os-cloud \
  --enable-nested-virtualization \
  --scopes=cloud-platform \
  --quiet
```

#### Using `screen` (essential for long builds)

The AOSP build takes 2-4 hours. Use `screen` so the build survives if your
browser closes, laptop sleeps, or internet drops.

```bash
# SSH into the VM
gcloud compute instances start aosp-builder-cutterfish \
  --project=$(gcloud config get-value project) \
  --zone=us-central1-a

gcloud compute ssh aosp-builder-cutterfish \
  --project=$(gcloud config get-value project) \
  --zone=us-central1-a  


gcloud compute instances list \
  --project=$(gcloud config get-value project) \
  --zone=us-central1-a  

sudo growpart /dev/sda 1
sudo resize2fs /dev/sda1
df -h

# Start a named screen session
screen -S aosp

# Now run all build commands inside screen...
# The build keeps running even if you disconnect.
```

**If you get disconnected:**

```bash
# Reattach to the running build session
screen -r aosp
```

### Step 1 — Install Build Dependencies

```bash
# Update system
sudo apt update && sudo apt upgrade -y

sudo apt-get install -y git-core gnupg flex bison build-essential \
    zip curl zlib1g-dev libc6-dev-i386 lib32ncurses-dev \
    x11proto-core-dev libx11-dev lib32z1-dev libgl1-mesa-dev \
    libxml2-utils xsltproc unzip fontconfig python3 \
    bridge-utils libvirt-daemon-system

# Install required packages
sudo apt install -y git repo git-lfs ccache libncurses5 libtinfo5 \
    android-tools-adb android-tools-fastboot \
    python3 python3-pip curl wget

# Configure git
git config --global user.name "Your Name"
git config --global user.email "your.email@example.com"

# Increase ccache size (recommended)
ccache -M 50G

```

### Step 2 — Download AOSP Android 14

**Important:** Use `android-14.0.0_r75`. The generated AIDL interfaces, Vehicle HAL
API surface, and SELinux policy format all target `aosp_level = 14`.
Using Android 13 or 15 will cause build failures.

```bash
git config --global user.name "nguyenngoctam1307"
git config --global user.email "nguyenngoctam1307@gmail.com"
git config --global color.ui true

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

cd ~/aosp-14-auto

# First try without the patch
source build/envsetup.sh

# Choose the target
lunch aosp_cf_x86_64_auto-trunk_staging-userdebug
```

### Step 3 — Build Cuttlefish Automotive Base Image

The `aosp_cf_x86_64_auto` target includes the full AAOS stack:
Car Service, Vehicle HAL framework, AAOS system UI.

```bash
cd ~/aosp-14-auto

# Fix platform_testing build error (missing test targets)
cd platform_testing
cat > /tmp/automotive_fix.mk << 'EOF'
ifeq ($(BOARD_IS_AUTOMOTIVE), true)
native_tests += \
    libwatchdog_test \
    evsmanagerd_test
endif
EOF
sed -i '/ifeq ($(BOARD_IS_AUTOMOTIVE)/,/endif/d' build/tasks/tests/native_test_list.mk
cat /tmp/automotive_fix.mk >> build/tasks/tests/native_test_list.mk
cd ~/aosp-14-auto


# Fixing sanbox
# Add your user to the 'disk' group (this is the most common fix)
sudo usermod -aG disk $USER

# Reload groups (important)
newgrp disk

# Strong fix for loop device / sandbox issue
export SOONG_GENRULE_SANDBOXING=false

# Reload groups again
sudo modprobe loop
sudo chown root:disk /dev/loop* 2>/dev/null || true
sudo chmod 666 /dev/loop* 2>/dev/null || true

source build/envsetup.sh
# MUST use _auto target for automotive
lunch aosp_cf_x86_64_auto-trunk_staging-userdebug

# First build (~2-4 hours)
m -j$(nproc)
```

### Step 3a — Install Cuttlefish Host Packages

Cuttlefish requires host-side packages (`crosvm`, `cvd` tools, networking) to launch
virtual devices. These must be installed **before** running `launch_cvd`.

```bash
# Install build dependencies
sudo apt install -y git devscripts equivs config-package-dev \
    debhelper-compat golang libarchive-tools net-tools opus-tools \
    xdg-utils iptables f2fs-tools ebtables

# Clone and build cuttlefish host packages (use a stable tag)
cd ~
git clone https://github.com/google/android-cuttlefish.git
cd android-cuttlefish
git checkout v1.50.1    # pinned — main branch may have Rust build failures
tools/buildutils/build_packages.sh

# Install the .deb packages
sudo dpkg -i ./cuttlefish-base_*.deb
sudo apt-get install -f -y
sudo dpkg -i ./cuttlefish-user_*.deb
sudo apt-get install -f -y

# Add user to required groups
sudo usermod -aG kvm,cvdnetwork,render $USER

# REBOOT (required for kernel modules and group changes)
sudo reboot
```

**After reboot, verify the installation:**

```bash
# All three groups must appear
groups $USER | grep -o 'kvm\|cvdnetwork\|render'

# KVM device must exist (nested virt on GCP)
ls -la /dev/kvm

# Cuttlefish capability check must be found
find /usr/lib/cuttlefish* -name "capability_query.py" 2>/dev/null
```

<details>
<summary>Troubleshooting: build_packages.sh fails with Rust/virtio-media errors</summary>

The `main` branch of `android-cuttlefish` may have Rust compilation errors
(e.g. `v4l2_requestbuffers has no field named flags`). This is a mismatch between
the `virtio-media` crate and the kernel headers on Ubuntu 22.04.

**Fix:** Use a pinned stable tag as shown above (`v1.50.1`). Check available tags:

```bash
git tag -l | sort -V | tail -10
```
</details>

### Step 4 — Upload output zips to GCS bucket

Upload output zips to a GCS bucket via the **browser**.

1. Go to [console.cloud.google.com/storage](https://console.cloud.google.com/storage)
2. Click **Create Bucket** → name it `aosp-thesis-temp` → Create
3. Click the bucket → **Upload Files** → select:
   - `output_c1.zip`, `output_c2.zip`, `output_c3.zip`, `output_c4.zip`
4. Grant the VM access to the bucket:
   - Click **Permissions** tab on the bucket
   - Click **Grant Access**
   - New principal: `YOUR_PROJECT_NUMBER-compute@developer.gserviceaccount.com`
     (find it in Cloud Shell: `gcloud projects describe $(gcloud config get-value project) --format="value(projectNumber)"`)
   - Role: **Storage Object Viewer**
   - Click **Save**

### Step 5 — Download files on VM and build

On the VM (already SSH'd in via `gcloud compute ssh`):

```bash
# Start screen if not already running
screen -S aosp

# Set project for GCS access (find your project ID with: gcloud config get-value project)
gcloud config set project $(gcloud config get-value project)

# Download output zips from the bucket
gcloud storage cp gs://aosp-thesis-temp/output_c1.zip ~/
gcloud storage cp gs://aosp-thesis-temp/output_c2.zip ~/
gcloud storage cp gs://aosp-thesis-temp/output_c3.zip ~/
gcloud storage cp gs://aosp-thesis-temp/output_c4.zip ~/

# Get fix script from GitHub
curl -o ~/apply_aosp14_fixes.sh \
    https://raw.githubusercontent.com/appdev1307/code-codegen-aosp-llm-based/main/apply_aosp14_fixes.sh
chmod +x ~/apply_aosp14_fixes.sh

# Unzip all conditions
unzip ~/output_c1.zip -d ~/output_c1
unzip ~/output_c2.zip -d ~/output_c2
unzip ~/output_c3.zip -d ~/output_c3
unzip ~/output_c4.zip -d ~/output_c4

# Verify files exist
find ~/output_c1 -name "*.aidl" -o -name "*.cpp" -o -name "*.te" | head -5
```

**Set up AOSP build environment:**

> **Warning:** Do not clone RAG `aosp_source/` inside the AOSP build tree.
> Soong will pick up duplicate `Android.bp` files and fail with "module already defined".
> If this happens: `mv ~/aosp-14-auto/aosp_source ~/aosp_source_rag`

```bash
cd ~/aosp-14-auto
source build/envsetup.sh
lunch aosp_cf_x86_64_auto-trunk_staging-userdebug

# Helper: clean previous condition's generated files
# IMPORTANT: only remove generated files — NOT AOSP originals like VehiclePropertyStatus.aidl
clean_hal() {
    rm -f hardware/interfaces/automotive/vehicle/aidl/android/hardware/automotive/vehicle/VehiclePropertyAdas.aidl
    rm -f hardware/interfaces/automotive/vehicle/aidl/android/hardware/automotive/vehicle/VehiclePropertyVss.aidl
    rm -f hardware/interfaces/automotive/vehicle/impl/VehicleHalService*.cpp
    rm -f hardware/interfaces/automotive/vehicle/impl/Android.bp.generated
    rm -f system/sepolicy/vendor/vehicle_hal_*.te
}

# Helper: restore AOSP tree to original state after a failed build
# AOSP uses repo (not git) — each subdirectory is its own git repo
restore_aosp() {
    echo "Restoring AOSP tree to original state..."
    cd ~/aosp-14-auto/hardware/interfaces && git checkout .
    cd ~/aosp-14-auto/system/sepolicy && git checkout .
    cd ~/aosp-14-auto
    echo "✓ AOSP tree restored"
}
```
**If a build fails — recovery steps:**

```bash
# 1. Restore the AOSP tree (undo all generated file changes)
restore_aosp

# 2. Verify the tree is clean
cd ~/aosp-14-auto/hardware/interfaces && git status
cd ~/aosp-14-auto/system/sepolicy && git status

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

### Step 6a — AIDL Frozen API Integration (required for new .aidl files)

Adding a new `.aidl` file (like `VehiclePropertyAdas.aidl`) to the frozen
`aidl_interface` module requires updating the API version. Without this,
the build fails with `frozen: true` hash mismatch errors.

**Proven approach (used for successful C4 build):**

```bash
cd ~/aosp-14-auto

# 1. Copy generated AIDL enum file
cp ~/output_c4/hardware/interfaces/automotive/vehicle/aidl/android/hardware/automotive/vehicle/VehiclePropertyAdas.aidl \
   hardware/interfaces/automotive/vehicle/aidl/android/hardware/automotive/vehicle/

# 2. Add to srcs in aidl_interface Android.bp
AIDL_BP=hardware/interfaces/automotive/vehicle/aidl/Android.bp
LAST_AIDL=$(grep -n '\.aidl"' "$AIDL_BP" | tail -1 | cut -d: -f1)
sed -i "${LAST_AIDL}a\\        \"android/hardware/automotive/vehicle/VehiclePropertyAdas.aidl\"," "$AIDL_BP"

# 3. Temporarily unfreeze the AIDL interface
sed -i 's/frozen: true,/frozen: false,/' "$AIDL_BP"

# 4. Clean build artifacts and update API
rm -rf out/
source build/envsetup.sh
lunch aosp_cf_x86_64_auto-trunk_staging-userdebug
m android.hardware.automotive.vehicle-update-api

# 5. Re-freeze the interface
sed -i 's/frozen: false,/frozen: true,/' "$AIDL_BP"

# 6. Add new package to VINTF FCM exclude list (types-only package)
sed -i '/static std::vector<std::string> excluded_exact{/a\            // LLM-generated types-only AIDL package\n            "android.hardware.automotive.vehicle@4",' \
    hardware/interfaces/compatibility_matrices/exclude/fcm_exclude.cpp

# 7. Clean and full build
rm -rf out/
source build/envsetup.sh
lunch aosp_cf_x86_64_auto-trunk_staging-userdebug
m -j$(nproc) 2>&1 | tee ~/build_full_c4.log
```

**Build result:**
```
[100% 4035/4035] touch out/soong/ndk_abi_diff.timestamp
#### build completed successfully (04:52 (mm:ss)) ####
```

**Step 6b — Verify AIDL was compiled into the API surface:**

```bash
# 1. Prove the AOSP AIDL compiler accepted the enum
cat out/soong/.intermediates/hardware/interfaces/automotive/vehicle/aidl/android.hardware.automotive.vehicle-api/dump/android/hardware/automotive/vehicle/VehiclePropertyAdas.aidl
# Should show: @Backing(type="int") @VintfStability enum VehiclePropertyAdas { ... }

# 2. Prove V4 frozen API snapshot contains the enum
cat hardware/interfaces/automotive/vehicle/aidl/aidl_api/android.hardware.automotive.vehicle/4/android/hardware/automotive/vehicle/VehiclePropertyAdas.aidl
# Should show same enum with all 50 property constants (0x1000–0x1031)

# 3. Verify all API versions exist
ls hardware/interfaces/automotive/vehicle/aidl/aidl_api/android.hardware.automotive.vehicle/
# Should show: 1  2  3  4  current

# 4. Save proof files for thesis
cp out/soong/.intermediates/hardware/interfaces/automotive/vehicle/aidl/android.hardware.automotive.vehicle-api/dump/android/hardware/automotive/vehicle/VehiclePropertyAdas.aidl \
   ~/VehiclePropertyAdas_compiled.aidl
```

**What this proves:** The AOSP AIDL toolchain (`aidl --dumpapi --structured --stability vintf`)
successfully parsed the LLM-generated `VehiclePropertyAdas.aidl` enum, validated it against
the AIDL grammar, and registered it as part of the `android.hardware.automotive.vehicle` V4
API surface. The full system image build completed without errors, confirming the generated
code integrates into the Android Automotive OS build system.

> **Note:** V4 modules (Java, NDK, Rust bindings) are not compiled into the image because
> no existing AOSP module depends on V4 yet. The V4 API surface is frozen and available for
> any module that adds `android.hardware.automotive.vehicle-V4-java` (or `-ndk`, `-rust`)
> to its dependencies. This is the standard AOSP workflow for new API versions.

**Common pitfalls:**

- **Never delete `aidl_api/`** — this removes frozen API snapshots and causes V3→V4 version cascade across the entire AOSP tree
- **Never manually sed V3→V4 in Android.bp files** — hundreds of modules depend on V3
- **Always `rm -rf out/`** between frozen/unfrozen transitions — stale ninja cache causes "multiple rules generate" errors
- **Follow the AOSP error message** — when it says "set `frozen: false` then run `update-api`", that is the correct fix

**Recovery if the tree gets into a bad state:**

```bash
cd ~/aosp-14-auto/hardware/interfaces && git checkout -- . && cd ~/aosp-14-auto
cd ~/aosp-14-auto/packages/services/Car && git checkout -- . && cd ~/aosp-14-auto
cd ~/aosp-14-auto/cts && git checkout -- . && cd ~/aosp-14-auto
cd ~/aosp-14-auto/device/generic/car && git checkout -- . && cd ~/aosp-14-auto
rm -rf out/
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
# Cuttlefish host packages must be installed first (see Step 3a above)
# Verify: groups $USER | grep -o 'kvm\|cvdnetwork\|render'

# Set up build environment (also adds adb to PATH)
cd ~/aosp-14-auto
source build/envsetup.sh
lunch aosp_cf_x86_64_auto-trunk_staging-userdebug

# Launch Cuttlefish (in a screen session recommended)
launch_cvd --noresume --cpus=4 --memory_mb=4096
# Wait for: VIRTUAL_DEVICE_BOOT_COMPLETED
```

```bash
# In a new terminal (source the build env again)
cd ~/aosp-14-auto
source build/envsetup.sh
lunch aosp_cf_x86_64_auto-trunk_staging-userdebug

# Check connected devices — Cuttlefish registers on two transports
adb devices
# Expected output:
#   0.0.0.0:6520    device
#   vsock:3:5555    device
# Use -s flag to target one device (avoids "more than one device" error)

# Verify VHAL backend is AIDL (required for Android 14)
adb -s 0.0.0.0:6520 shell cmd car_service get-vhal-backend
# Expected: Vehicle HAL backend: AIDL

# Test Vehicle HAL read/write
adb -s 0.0.0.0:6520 shell cmd car_service get-property-value PERF_VEHICLE_SPEED
# Expected: HalPropValue{..., Value: 0.0 METER_PER_SEC}

adb -s 0.0.0.0:6520 shell cmd car_service get-carpropertyconfig PERF_VEHICLE_SPEED
# Expected: access:READ, changeMode:CONTINUOUS, valueType:FLOAT

# Verify ADAS properties are present in the base image
adb -s 0.0.0.0:6520 shell cmd car_service get-property-value FORWARD_COLLISION_WARNING_ENABLED
# Expected: HalPropValue{..., Value: TRUE}

adb -s 0.0.0.0:6520 shell cmd car_service get-property-value CRUISE_CONTROL_ENABLED
# Expected: HalPropValue{..., Value: TRUE}

# Test VHAL event injection (simulate enabling/disabling a property)
adb -s 0.0.0.0:6520 shell cmd car_service inject-vhal-event CRUISE_CONTROL_ENABLED 0 true

# List all VHAL property IDs (250+ properties in a full AAOS build)
adb -s 0.0.0.0:6520 shell cmd car_service list-vhal-props

# Dump all ADAS-related properties
adb -s 0.0.0.0:6520 shell dumpsys car_service > ~/car_service_dump.txt
grep -i "adas\|cruise\|lane\|collision\|emergency\|blind_spot" ~/car_service_dump.txt
# Expected ADAS properties in base image:
#   AUTOMATIC_EMERGENCY_BRAKING_ENABLED/STATE
#   FORWARD_COLLISION_WARNING_ENABLED/STATE
#   BLIND_SPOT_WARNING_ENABLED
#   LANE_DEPARTURE_WARNING_ENABLED/STATE
#   LANE_KEEP_ASSIST_ENABLED/STATE
#   LANE_CENTERING_ASSIST_ENABLED/COMMAND
#   EMERGENCY_LANE_KEEP_ASSIST_ENABLED
#   CRUISE_CONTROL_ENABLED
#   LOW_SPEED_COLLISION_WARNING_ENABLED
#   LOW_SPEED_AUTOMATIC_EMERGENCY_BRAKING_ENABLED

# Verify SELinux is enforcing (production mode)
adb -s 0.0.0.0:6520 shell getenforce
# Expected: Enforcing

# Check SELinux denials (requires root on userdebug builds)
adb -s 0.0.0.0:6520 root
adb -s 0.0.0.0:6520 shell dmesg | grep avc
# No Vehicle HAL related denials expected in base image

# Access the WebRTC display from your local machine:
# gcloud compute ssh aosp-builder --zone=us-central1-a -- -L 8443:localhost:8443
# Then open https://localhost:8443 in your browser

# Run VTS
atest VtsHalAutomotiveVehicle

# Shutdown
stop_cvd
```

### Step 8 — C5 Runtime Validation (FakeVHAL + VTS + HMI App)

After C1-C4 generation is complete and the base AOSP image is built, run C5 to extend
the VHAL with VSS properties and validate them at runtime on Cuttlefish.

#### 8a — Prepare AOSP assets on GCP VM

```bash
# Grant GCS write access first (run in Cloud Shell, not VM)
gcloud storage buckets add-iam-policy-binding gs://aosp-thesis-temp \
  --member="serviceAccount:751513866050-compute@developer.gserviceaccount.com" \
  --role="roles/storage.objectAdmin"

BUCKET=gs://aosp-thesis-temp
cd ~/aosp-14-auto

# 1. FakeVehicleHardware source
gsutil cp \
  hardware/interfaces/automotive/vehicle/aidl/impl/fake_impl/hardware/src/FakeVehicleHardware.cpp \
  $BUCKET/FakeVehicleHardware.cpp

# 2. AOSP compiled AIDL property IDs (-j = no directory paths in zip)
DUMP_DIR=out/soong/.intermediates/hardware/interfaces/automotive/vehicle/aidl/android.hardware.automotive.vehicle-api/dump/android/hardware/automotive/vehicle
zip -j ~/aosp_dump.zip $DUMP_DIR/VehicleProperty*.aidl
gsutil cp ~/aosp_dump.zip $BUCKET/aosp_dump.zip

# Verify
gsutil ls $BUCKET/
```

#### 8b — Run C5 on Colab

```python
# Authenticate GCS
from google.colab import auth
auth.authenticate_user()
```

```bash
# Copy FakeVehicleHardware (aosp_source/ already exists from RAG step)
gsutil cp gs://aosp-thesis-temp/FakeVehicleHardware.cpp aosp_source/

# Download compiled AIDL property IDs
mkdir -p aosp_dump
gsutil cp gs://aosp-thesis-temp/aosp_dump.zip .
unzip aosp_dump.zip -d aosp_dump_raw
cp aosp_dump_raw/VehicleProperty*.aidl aosp_dump/

# Run C5 (reads C4 output automatically, generates 3 artifact types)
python multi_main_c5.py
# Outputs: output_c5/fake_vhal/ + output_c5/vts/ + output_c5/hmi_app/

# Upload to GCS
zip -r output_c5.zip output_c5/
gsutil cp output_c5.zip gs://aosp-thesis-temp/
```

#### 8c — Apply C5 patch and rebuild on GCP VM

```bash
# Download C5 output
gsutil cp gs://aosp-thesis-temp/output_c5.zip ~/
unzip ~/output_c5.zip -d ~/output_c5

cd ~/aosp-14-auto
source build/envsetup.sh
lunch aosp_cf_x86_64_auto-trunk_staging-userdebug

# Copy VTS tests
mkdir -p test/vts/vss_vehicle
cp ~/output_c5/vts/* test/vts/vss_vehicle/


# Rebuild affected modules only (~30 min vs full rebuild)
mmm test/vts/vss_vehicle

m vendorimage
cvd reset -y
launch_cvd -gpu_mode=guest_swiftshader --cpus 4 --memory_mb 8192

# JSON file in other terminal
# from mac
gcloud compute scp /Users/macintoshhd/Downloads/VssProperties.json nguyenngoctam1307@aosp-builder-cutterfish:~/aosp-14-auto/ --zone=us-central1-a
# from output_c5.

adb root
adb remount
adb sync vendor
adb reboot
```

#### 8d — Relaunch Cuttlefish and run VTS

```bash
# Relaunch with new build
cd ~/aosp-14-auto
source build/envsetup.sh
lunch aosp_cf_x86_64_auto-trunk_staging-userdebug

#
adb -s 0.0.0.0:6520 shell pgrep -f vehicle
adb -s 0.0.0.0:6520 shell dumpsys android.hardware.automotive.vehicle.IVehicle/default --list | head -20

adb -s 0.0.0.0:6520 shell setenforce 0
adb -s 0.0.0.0:6520 shell dumpsys car_service | grep -icE "VENDOR_PROPERTY\(0x2[0-9a-f]{7}\)"

# Verify VSS properties are now served
adb -s 0.0.0.0:6520 shell cmd car_service get-property-value 0x1000 0
# Expected: HalPropValue{prop=4096, areaId=0, value=...}
# (0x1000 = ADAS domain first property)

adb -s 0.0.0.0:6520 shell cmd car_service get-property-value 0x2000 0
# Expected: HalPropValue{prop=8192, areaId=0, value=...}
# (0x2000 = Body domain first property)

# Run VSS VTS tests
atest VtsHalAutomotiveVehicleVss
# Expected: compile-time enum tests PASS, runtime access tests PASS

# Inject VSS signal and verify
adb -s 0.0.0.0:6520 shell cmd car_service inject-vhal-event 0x1000 0 42
adb -s 0.0.0.0:6520 shell cmd car_service get-property-value 0x1000 0
# Expected: value=42
```

#### 8e — Install and verify HMI app

```bash
# Build HMI app inside AOSP tree
mkdir -p packages/apps/VssDashboard
cp -r ~/output_c5/hmi_app/src \
      ~/output_c5/hmi_app/AndroidManifest.xml \
      ~/output_c5/hmi_app/Android.bp \
      packages/apps/VssDashboard/
mmm packages/apps/VssDashboard

# Install on Cuttlefish
adb -s 0.0.0.0:6520 install -r \
  out/target/product/vsoc_x86_64/system/app/VssDashboardApp/VssDashboardApp.apk

# Launch app
adb -s 0.0.0.0:6520 shell am start -n com.vss.vehicleapp/.MainActivity

# Inject values and check app UI updates via logcat
adb -s 0.0.0.0:6520 shell cmd car_service inject-vhal-event 0x1000 0 99
adb -s 0.0.0.0:6520 logcat | grep -i "vehicleapp\|CarProperty\|onChangeEvent"
# Expected: onChangeEvent fired with prop=4096 value=99

# FastAPI Backend (optional — for REST API testing)
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

# Delete GCS bucket if you created one
gsutil rm -r gs://aosp-thesis-temp 2>/dev/null

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
| 1 | AIDL (VehiclePropertyAdas.aidl) | Python AIDL parser | `aidl --structured --stability vintf` | None (additive file) |
| 2 | C++ | `clang++ -fsyntax-only` | `mmm` (Soong/clang) | None (AIDL-only prompts) |
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
├── multi_main_c5.py               # C5: Advanced runtime validation pipeline
│                                  #     (FakeVHAL patch + VTS tests + HMI app, combined)
├── agents/                        # Generation agents
│   ├── rag_dspy_mixin.py          #   RAG+DSPy shared logic (reused by C5)
│   ├── rag_dspy_architect_agent.py
│   ├── rag_dspy_aidl_agent.py     #   Domain-specific base addresses (DOMAIN_BASE)
│   ├── rag_dspy_cpp_agent.py      #   Reused by C5 FakeVHAL patcher
│   ├── rag_dspy_selinux_agent.py
│   ├── rag_dspy_backend_agent.py
│   └── ...
├── dspy_opt/                      # DSPy optimiser
│   ├── optimizer.py               #   MIPROv2 runner (requires optuna)
│   ├── hal_modules.py             #   Module registry
│   ├── hal_signatures.py          #   DSPy Signatures (domain bases in AIDLSignature)
│   ├── metrics.py                 #   Scoring functions
│   ├── validators.py              #   Syntax validators (clang, checkpolicy, etc.)
│   └── saved/                     #   Optimised programs (12 JSON files, reused by C5)
├── rag/                           # RAG system
│   ├── aosp_indexer.py            #   AOSP → ChromaDB indexer (HIDL exclusion)
│   ├── aosp_retriever.py          #   Query retriever (reused by C5)
│   └── chroma_db/                 #   Vector database (7 collections, ~17.6K chunks)
├── dataset/
│   └── vss.json                   # Vehicle Signal Specification (1571 signals)
├── aosp_dump/                     # Compiled AIDL property IDs from GCP VM (for C5)
│   └── VehicleProperty*.aidl      #   One per domain, contains hex IDs
├── aosp_source/                   # AOSP source files copied from GCP VM (for C5)
│   └── FakeVehicleHardware.cpp    #   Original — C5 patches and returns modified version
├── experiments/results/           # Analysis outputs
│   ├── matched_analysis.md        #   4-condition comparison (C1-C4)
│   ├── comparison.json
│   ├── latex_table.tex
│   └── final_analysis.md
├── apply_aosp14_fixes.sh          # Automated AOSP 14 integration fixes
├── apply_chroma_fix.py            # Patches C3/C4 to use ChromaDB singleton
├── fix_chroma_singleton.py        # ChromaDB singleton + monkey-patch
├── validate_aosp_build.sh         # AOSP build validation → JSON report
├── output/                        # C1 output
├── output_adaptive/               # C2 output
├── output_rag_dspy/               # C3 output
├── output_c4_feedback/            # C4 output (primary input for C5)
└── output_c5/                # C5 output
    ├── fake_vhal/
    │   └── FakeVehicleHardware_vss_patch.cpp  # Patched to serve VSS properties
    ├── vts/
    │   ├── VtsHalAutomotiveVehicleVss.cpp      # Custom VSS VTS tests
    │   ├── Android.bp
    │   └── VtsHalAutomotiveVehicleVss.xml
    ├── hmi_app/                               # HMI app with real CarPropertyManager IDs
    │   ├── AndroidManifest.xml
    │   ├── Android.bp
    │   └── src/main/
    │       ├── java/com/vss/vehicleapp/
    │       └── res/layout/
    └── c5_results.json                        # Scores per agent
```

## Latest Results (500 signals, matched agents)

| Condition | Avg Score | Syntax | Coverage | Effect vs C1 |
|-----------|-----------|--------|----------|-------------|
| C1 Baseline | 0.817 | 0.912 | 0.518 | — |
| C2 Adaptive | 0.803 | 0.886 | 0.526 | r = -0.015 |
| C3 RAG+DSPy | 0.858 | 0.898 | 0.679 | r = 0.169 |
| C4 Feedback | **0.876** | **0.924** | **0.692** | r = 0.245 * |

Kruskal-Wallis H = 8.32, p = 0.040 (significant at α = 0.05).
C1 vs C4 pairwise: U = 1651.0, p = 0.016 *, r = 0.245 (small-to-medium effect size).
C2 vs C4 pairwise: U = 1267.5, p = 0.024 *, r = 0.243 (small effect size).

### Statistical significance

The monotonic improvement C1 < C3 < C4 is consistent across all dimensions and
statistically confirmed in the 500-signal experiment (Kruskal-Wallis p = 0.040).
C2 scores slightly below C1 in the 500-signal run due to template fallbacks in the
Android app agent at larger module sizes — the chunked generation architecture of C1/C2
degrades at scale compared to C3/C4's DSPy-based single-call generation.
C3→C4 improvement is not individually significant (p = 0.723) but C4 shows the largest
gain over baseline.

## Known Issues

- **Batch labelling mismatch:** LLM returns 1 signal per batch instead of 4; remaining are padded. This is a prompt/parsing issue in the labelling code — the LLM returns a single JSON object instead of an array.
- **AOSP version:** Generated code targets Android 14 only — do not use Android 13 or 15 source trees. AIDL interfaces and SELinux policy format differ across major versions.
- **Do not clone `aosp_source/` inside the AOSP build tree.** Soong scans all directories for `Android.bp` files and will fail with "module already defined" if it finds duplicates.
- **`clean_hal` must not glob `VehicleProperty*.aidl`** — this deletes AOSP originals like `VehiclePropertyStatus.aidl`, `VehiclePropertyAccess.aidl`, `VehiclePropertyChangeMode.aidl` which breaks the AIDL build. Only delete specific generated files (`VehiclePropertyAdas.aidl`, `VehiclePropertyVss.aidl`).
- **Overlapping domain property IDs:** C1-C4 generate per-domain AIDL enums starting at `0x1000` for each domain. The updated `rag_dspy_aidl_agent.py` uses domain-specific base addresses (ADAS=0x1000, Body=0x2000, Cabin=0x3000, etc.) to prevent ID conflicts. Re-run C1-C4 after this fix to get globally unique IDs.
- **SELinux feedback loop ineffective:** The C4 feedback loop cannot fix SELinux errors because (a) `checkpolicy` reports missing external macro definitions that exist only in the full AOSP sepolicy tree, and (b) the `validation_feedback` field is not in the SELinux DSPy Signature, so error messages are silently dropped. This is documented as a known limitation — SELinux policies pass AOSP build validation via `apply_aosp14_fixes.sh`.
- **C5 requires AOSP source files:** `multi_main_c5.py` needs `FakeVehicleHardware.cpp` from the GCP VM and compiled property IDs from the AOSP build dump. Copy them via GCS before running C5. Without them, C5 generates a stub FakeVehicleHardware that won't compile against the full AOSP tree.
- **C5 FakeVHAL patch requires AOSP rebuild:** After applying the FakeVehicleHardware patch, a full `mmm` rebuild (~30 min) and Cuttlefish relaunch are required before VSS properties become accessible at runtime.
- **gsutil in Colab:** Run `from google.colab import auth; auth.authenticate_user()` before any `gsutil` commands in Colab notebooks.

## Key Design Decisions

- **AIDL-only agent prompts:** All code generation agents (AIDL, C++, SELinux, Android.bp) have explicit Android 14 AIDL constraints in their system prompts. This prevents HIDL pattern generation at the source — from C1 through C4. The AIDL agent generates additive `VehiclePropertyAdas.aidl` (not replacement files), the C++ agent requires `aidl::` namespace and `BnIVehicle`, the SELinux agent follows `hal_vehicle_default.te` structure, and the Android.bp agent requires `vendor: true` and AIDL libraries.
- **3-layer HIDL defense:** Layer 1 (indexer) — `aosp_indexer.py` excludes HIDL files from ChromaDB using path patterns and lowercased content keywords. Layer 2 (mixin) — `rag_dspy_mixin.py` filters HIDL-contaminated chunks at retrieval time before they reach the LLM prompt. Layer 3 (agent) — `rag_dspy_aidl_agent.py` injects explicit AIDL-only constraints (`no V2_0`, `no oneway`, `no out params`, `boolean not bool`) into every generation call. This 3-layer approach ensures clean AIDL output regardless of RAG corpus quality or LLM training data.
- **HIDL exclusion in RAG corpus:** The AOSP source tree contains both HIDL (Android 12/13) and AIDL (Android 14) Vehicle HAL implementations. Without filtering, the RAG retriever returns HIDL examples that outnumber AIDL examples in simplicity, causing the LLM to generate legacy `#include <hidl/Status.h>` and `vehicle/2.0/IVehicle.h` patterns that don't compile in Android 14. The indexer excludes all `/2.0/`, `/1.0/`, `V2_0`, and `/hidl/` paths, ensuring only AIDL-compatible patterns are retrieved.
- **ChromaDB singleton:** Multiple agents sharing the same ChromaDB path causes "instance already exists" errors. `fix_chroma_singleton.py` monkey-patches `chromadb.PersistentClient` to return a shared singleton. Both C3 and C4 apply this patch at startup via `apply_chroma_fix.py`.
- **C4 post-validation architecture:** C4 uses `architect.run()` identically to C3 for initial generation, then post-validates output files and retries only failed agents. Error feedback goes into a separate prompt field (not appended to `aosp_context`) to avoid polluting RAG context. This ensures C4's floor is always ≥ C3.
- **Version-pinned RAG corpus:** The RAG source must be cloned with the same tag (`android-14.0.0_r75`) as the AOSP build tree. Mismatched versions cause the LLM to generate patterns (Android.bp `srcs` lists, API freeze hashes, AIDL module structure) that don't match the build system's expectations.
- **Additive vs replacement AIDL:** C1/C2 (without RAG) previously generated replacement AIDL files that overwrote existing AOSP interfaces. With the prompt fix, all conditions now generate additive `VehiclePropertyAdas.aidl` that complements existing code.
- **Automated AOSP 14 fixes:** `apply_aosp14_fixes.sh` handles systematic integration gaps (vendor:true, SELinux type declarations) that are predictable and automatable rather than code quality issues.
- **C5 dynamic property loading:** `multi_main_c5.py` reads compiled property IDs from the AOSP build dump, property types and access modes from C4's YAML spec, and domain groupings from MODULE_PLAN.json — fully dynamic for any signal count or module configuration. Falls back to hardcoded Cuttlefish property map if inputs are unavailable.
- **C5 non-destructive FakeVHAL patching:** The FakeVehicleHardware patcher appends a `kVssProperties` vector and injects a single `mergeVssProperties()` call into `getAllPropertyConfigs()` — never replacing existing code. This minimises compilation risk and makes the patch reversible by deleting the appended block.
- **Domain-specific AIDL base addresses:** `rag_dspy_aidl_agent.py` uses a `DOMAIN_BASE` dict (ADAS=0x1000, Body=0x2000, Cabin=0x3000, Chassis=0x4000, HVAC=0x5000, Infotainment=0x6000, Powertrain=0x7000) to ensure globally unique property IDs across domains, enabling CarPropertyManager to distinguish properties without conflict.

## License

Research use only — MSE thesis project.