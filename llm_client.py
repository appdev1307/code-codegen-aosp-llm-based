import json
import requests
from pathlib import Path
from typing import Any, Dict, List, Optional

OLLAMA_URL = "http://127.0.0.1:11434/api/generate"
MODEL = "qwen2.5-coder:7b"
TIMEOUT = 600

DEBUG_DIR = Path("output/.llm_draft/latest")
DEBUG_DIR.mkdir(parents=True, exist_ok=True)

def call_llm(
    prompt: str,
    system: str = "",
    *,
    stream: bool = False,
    temperature: float = 0.0,
    top_p: float = 1.0,
    stop: Optional[List[str]] = None,
    response_format: Optional[str] = None,
) -> str:
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
    if response_format is not None:
        payload["format"] = response_format

    resp = requests.post(OLLAMA_URL, json=payload, stream=stream, timeout=TIMEOUT)
    resp.raise_for_status()

    if not stream:
        data = resp.json()

        # dump full HTTP response so we can see what server really returns
        (DEBUG_DIR / "OLLAMA_HTTP_LAST.json").write_text(
            json.dumps(data, indent=2, ensure_ascii=False), encoding="utf-8"
        )

        # critical: return ONLY generated text
        return data.get("response", "") or ""

    chunks: List[str] = []
    http_lines: List[dict] = []

    for line in resp.iter_lines(decode_unicode=True):
        if not line:
            continue
        obj = json.loads(line)
        http_lines.append(obj)
        if "response" in obj:
            chunks.append(obj["response"])
        if obj.get("done"):
            break

    (DEBUG_DIR / "OLLAMA_HTTP_LAST_STREAM.json").write_text(
        json.dumps(http_lines, indent=2, ensure_ascii=False), encoding="utf-8"
    )

    return "".join(chunks)
