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

#ollama run qwen2.5-coder:7b # RAM size > 32GB
#ollama pull deepseek-coder:6.7b
#ollama run deepseek-coder:6.7b
# Differen terminal
#export OLLAMA_NUM_PARALLEL=1
#export OLLAMA_MAX_LOADED_MODELS=1
#export OLLAMA_KEEP_ALIVE=15m
#ollama run deepseek-coder:6.7b


python3 -m venv .venv
source .venv/bin/activate

pip install --upgrade pip

pip install -r requirements.txt

#python main.py
python multi_main.py

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