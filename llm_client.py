# FILE: llm_client.py
import json
import requests
from typing import Optional, Dict, Any

OLLAMA_URL = "http://localhost:11434/api/generate"
MODEL = "deepseek-coder:6.7b"
TIMEOUT = 600  # seconds

DEFAULT_SYSTEM = (
    "You are a senior software engineer.\n"
    "Follow instructions exactly.\n"
    "Do not ask questions; make reasonable assumptions when required.\n"
    "When asked for multi-file output, emit ONLY the requested file blocks.\n"
)

DEFAULT_OPTIONS = {
    "temperature": 0.2,
    "top_p": 0.9,
    "num_predict": 2048,
}


def call_llm(prompt: str, system: str = "", options: Optional[Dict[str, Any]] = None) -> str:
    if not isinstance(prompt, str) or not prompt.strip():
        raise ValueError("call_llm(): prompt is empty")

    sys_msg = system.strip() if system and system.strip() else DEFAULT_SYSTEM
    opts = dict(DEFAULT_OPTIONS)
    if options:
        opts.update(options)

    payload = {
        "model": MODEL,
        "prompt": prompt,
        "system": sys_msg,
        "stream": True,
        "options": opts,
    }

    resp = requests.post(OLLAMA_URL, json=payload, stream=True, timeout=TIMEOUT)
    resp.raise_for_status()

    chunks = []
    last_error: Optional[str] = None

    for line in resp.iter_lines(decode_unicode=True):
        if not line:
            continue
        try:
            data = json.loads(line)
        except json.JSONDecodeError:
            continue

        if isinstance(data, dict):
            if data.get("error"):
                last_error = str(data["error"])
            if data.get("response"):
                chunks.append(data["response"])
            if data.get("done") is True:
                break

    text = "".join(chunks).strip()

    if not text and last_error:
        raise RuntimeError(f"Ollama error: {last_error}")

    if not text:
        raise RuntimeError("LLM returned empty output")

    return text
