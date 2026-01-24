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

```