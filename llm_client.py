# FILE: llm_client.py

import json
import requests
from typing import Any, Dict, List, Optional

# Colab: be explicit
OLLAMA_URL = "http://127.0.0.1:11434/api/generate"
MODEL = "qwen2.5-coder:7b"
TIMEOUT = 600  # seconds


def call_llm(
    prompt: str,
    system: str = "",
    *,
    stream: bool = False,
    temperature: float = 0.0,
    top_p: float = 1.0,
    stop: Optional[List[str]] = None,
    response_format: Optional[str] = None,  # IMPORTANT: default OFF
) -> str:
    """
    Ollama client.

    Notes:
    - stream=False is recommended for structured outputs (JSON / file blocks)
    - temperature=0 reduces chatty/apology responses
    - DO NOT default to format=json; some Ollama versions/models reject it ("Invalid request")
    """

    payload: Dict[str, Any] = {
        "model": MODEL,
        "prompt": prompt,
        "system": system or "",
        "stream": stream,
        "options": {
            "temperature": temperature,
            "top_p": top_p,
        },
    }

    if stop:
        payload["options"]["stop"] = stop

    # Only include "format" if explicitly requested
    if response_format is not None:
        payload["format"] = response_format

    resp = requests.post(OLLAMA_URL, json=payload, stream=stream, timeout=TIMEOUT)
    resp.raise_for_status()

    if not stream:
        data = resp.json()
        return data.get("response", "") or ""

    chunks: List[str] = []
    for line in resp.iter_lines(decode_unicode=True):
        if not line:
            continue
        data = json.loads(line)
        if "response" in data:
            chunks.append(data["response"])
        if data.get("done"):
            break

    return "".join(chunks)
