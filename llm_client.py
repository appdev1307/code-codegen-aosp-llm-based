import json
import requests
from pathlib import Path
from typing import Any, Dict, List, Optional
import time

# Updated model - Llama 3.1 70B (excellent for coding + structured output)
MODEL = "llama3.1:70b"          # Change this if you use a specific quant tag, e.g. "llama3.1:70b-instruct-q4_0"

OLLAMA_URL = "http://127.0.0.1:11434/api/generate"
TIMEOUT = 1200                  # Increased timeout for 70B model (can be slow on large prompts)
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
            "num_ctx": 131072,      # Max context for Llama 3.1 (128K tokens) - important for your 200 properties!
        },
    }
    if stop:
        payload["options"]["stop"] = stop
    if response_format is not None:
        payload["format"] = response_format   # Keep for JSON mode

    resp = requests.post(OLLAMA_URL, json=payload, stream=stream, timeout=TIMEOUT)
    resp.raise_for_status()

    if not stream:
        data = resp.json()
        (DEBUG_DIR / "OLLAMA_HTTP_LAST.json").write_text(
            json.dumps(data, indent=2, ensure_ascii=False), encoding="utf-8"
        )
        return data.get("response", "") or ""

    # Streaming handling (unchanged)
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


# Optional: Add a safe JSON wrapper if you're using response_format="json"
def call_llm_json(prompt: str, system: str = "", max_retries: int = 5) -> dict:
    for attempt in range(max_retries):
        try:
            raw = call_llm(
                prompt=prompt,
                system=system,
                temperature=0.0,
                response_format="json",
            )
            # Clean common markdown wrappers
            raw = raw.strip()
            if raw.startswith("```json"):
                raw = raw[7:].strip()
            if raw.endswith("```"):
                raw = raw[:-3].strip()

            data = json.loads(raw)
            return data
        except json.JSONDecodeError as e:
            print(f"[LLM] JSON parse failed (attempt {attempt+1}/{max_retries}): {e}")
            (DEBUG_DIR / f"failed_json_attempt_{attempt}.txt").write_text(raw)
            time.sleep(2)
    raise ValueError("Failed to get valid JSON from LLM after retries")