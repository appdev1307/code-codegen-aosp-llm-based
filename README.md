# AI Code Generator – Android Automotive (Local + Validator)

This version adds a **Validator Agent** to simulate CTS/VTS-style checks
for generated VHAL, CarService, and SELinux code.

## Pipeline
Requirement → Code Generation → Validator → Output

## Run
```bash
brew install ollama

ollama serve
ollama list
#ollama run qwen2.5-coder:7b # RAM size > 32GB

ollama pull deepseek-coder:6.7b
ollama run deepseek-coder:6.7b

# Differen terminal
export OLLAMA_NUM_PARALLEL=1
export OLLAMA_MAX_LOADED_MODELS=1
export OLLAMA_KEEP_ALIVE=15m
ollama run deepseek-coder:6.7b


python3 -m venv .venv
source .venv/bin/activate

pip install --upgrade pip

pip install -r requirements.txt

python main.py
python multi_main.py

```