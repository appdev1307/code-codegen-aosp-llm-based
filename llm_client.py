import requests
import json

OLLAMA_URL = "http://localhost:11434/api/generate"
MODEL = "deepseek-coder:6.7b"
TIMEOUT = 600  # seconds


def call_llm(prompt: str, system: str = "") -> str:
    """
    Streaming LLM client for Ollama
    SINGLE public API used by all agents
    """

    payload = {
        "model": MODEL,
        "prompt": prompt,
        "system": system,
        "stream": True,
    }

    resp = requests.post(
        OLLAMA_URL,
        json=payload,
        stream=True,          # ✅ REQUIRED
        timeout=TIMEOUT,
    )

    resp.raise_for_status()

    chunks = []

    for line in resp.iter_lines(decode_unicode=True):
        if not line:
            continue

        data = json.loads(line)

        if "response" in data:
            chunks.append(data["response"])

        if data.get("done"):
            break

    return "".join(chunks)
