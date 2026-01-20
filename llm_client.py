# llm_client.py - Optimized for qwen2.5-coder:32b (and similar large coder models)
import json
import requests
from pathlib import Path
from typing import Any, Dict, List, Optional
import time

# === CONFIGURATION ===
MODEL = "qwen2.5-coder:32b"        # Perfect choice! Keep this
OLLAMA_URL = "http://127.0.0.1:11434/api/generate"
TIMEOUT = 1800                     # 30 minutes — safer for long generations with 32B
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
            "num_ctx": 32768,       # Qwen2.5 supports 32K context natively (128K with RoPE scaling, but 32K is stable)
            "num_predict": -1,      # Unlimited tokens — important for long AIDL/C++ output
            "num_batch": 512,       # Helps with long generations
        },
    }

    if stop:
        payload["options"]["stop"] = stop

    # Qwen2.5-Coder handles JSON mode very well — use it!
    if response_format == "json":
        payload["format"] = "json"

    try:
        resp = requests.post(OLLAMA_URL, json=payload, stream=stream, timeout=TIMEOUT)
        resp.raise_for_status()
    except requests.exceptions.RequestException as e:
        raise RuntimeError(f"Ollama request failed: {e}")

    if not stream:
        data = resp.json()
        (DEBUG_DIR / "OLLAMA_HTTP_LAST.json").write_text(
            json.dumps(data, indent=2, ensure_ascii=False), encoding="utf-8"
        )
        return data.get("response", "") or ""

    # Streaming mode
    chunks: List[str] = []
    full_response_lines: List[dict] = []

    for line in resp.iter_lines(decode_unicode=True):
        if not line:
            continue
        try:
            obj = json.loads(line)
            full_response_lines.append(obj)
            if "response" in obj:
                chunks.append(obj["response"])
            if obj.get("done"):
                break
        except json.JSONDecodeError:
            continue  # Skip malformed lines (rare but happens)

    (DEBUG_DIR / "OLLAMA_HTTP_LAST_STREAM.json").write_text(
        json.dumps(full_response_lines, indent=2, ensure_ascii=False), encoding="utf-8"
    )
    return "".join(chunks)


def call_llm_json(prompt: str, system: str = "", max_retries: int = 6) -> dict:
    """
    Reliable JSON extraction with cleaning + retries.
    Qwen2.5-Coder is excellent at JSON — this will almost always succeed on first try.
    """
    for attempt in range(max_retries):
        try:
            raw = call_llm(
                prompt=prompt,
                system=system,
                temperature=0.0,
                response_format="json",  # Enforced
            )

            raw = raw.strip()

            # Clean common wrappers (even though Qwen rarely does this)
            if raw.startswith("```json"):
                raw = raw[7:]
            if raw.endswith("```"):
                raw = raw[:-3]
            raw = raw.strip()

            if not raw:
                raise ValueError("Empty response after cleaning")

            data = json.loads(raw)
            return data

        except json.JSONDecodeError as e:
            print(f"[LLM] JSON parse failed (attempt {attempt+1}/{max_retries}): {e}")
            if attempt < max_retries - 1:
                (DEBUG_DIR / f"failed_json_attempt_{attempt+1}.txt").write_text(raw)
                time.sleep(3)  # Slight delay before retry
            else:
                raise ValueError(f"Failed to get valid JSON after {max_retries} attempts") from e
        except Exception as e:
            print(f"[LLM] Unexpected error: {e}")
            if attempt == max_retries - 1:
                raise
            time.sleep(3)