# FILE: llm_client.py

import json
import requests
from typing import Any, Dict, List, Optional

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
    response_format: Optional[str] = None,  # keep OFF
    num_predict: int = 2048,
    repeat_penalty: float = 1.1,
) -> str:
    """
    Ollama client for structured codegen.
    Use stream=False for best schema compliance.
    """

    payload: Dict[str, Any] = {
        "model": MODEL,
        "prompt": prompt,
        "system": system or "",
        "stream": stream,
        "options": {
            "temperature": temperature,
            "top_p": top_p,
            "num_predict": num_predict,
            "repeat_penalty": repeat_penalty,
        },
    }

    if stop:
        payload["options"]["stop"] = stop

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
