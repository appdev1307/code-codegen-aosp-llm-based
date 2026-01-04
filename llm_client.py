import requests
import json

OLLAMA_URL = "http://localhost:11434/api/generate"
MODEL = "deepseek-coder:6.7b"
TIMEOUT = 600  # seconds


def call_llm(prompt: str, system: str = "") -> str:
    """
    Streaming LLM client for Ollama
    - NO content printed to console
    - Streaming-safe
    - Enterprise / CI friendly
    """
    print("[DEBUG] Calling LLM...", flush=True)

    payload = {
        "model": MODEL,
        "prompt": prompt,
        "system": system,
        "stream": True,
    }

    resp = requests.post(
        OLLAMA_URL,
        json=payload,
        stream=True,      # ✅ ĐÚNG streaming mode
        timeout=TIMEOUT,
    )

    resp.raise_for_status()

    chunks = []

    for line in resp.iter_lines():
        if not line:
            continue

        data = json.loads(line.decode("utf-8"))

        if "response" in data:
            chunks.append(data["response"])   # ✅ CHỈ THU, KHÔNG PRINT

        if data.get("done"):
            break

    print("[DEBUG] LLM call finished", flush=True)
    return "".join(chunks)
