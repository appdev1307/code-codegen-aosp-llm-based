# llm_client.py - Optimized for qwen2.5-coder:32b (and similar large coder models)
# ENHANCED: Now supports configurable timeout per call
import json
import requests
from pathlib import Path
from typing import Any, Dict, List, Optional
import time

# === CONFIGURATION ===
MODEL = "qwen2.5-coder:32b"        # Perfect choice! Keep this
OLLAMA_URL = "http://127.0.0.1:11434/api/generate"
DEFAULT_TIMEOUT = 1200             # 20 minutes - realistic for complex 32B generations
DEBUG_DIR = Path("output/.llm_draft/latest")
DEBUG_DIR.mkdir(parents=True, exist_ok=True)

# SPECIFIC TIMEOUTS FOR DIFFERENT TASKS
# These are referenced in documentation but can be used by agents
TIMEOUT_BUILD_FILES = 600    # 10 min - Build files are simpler
TIMEOUT_DESIGN_DOCS = 600    # 10 min - Design documents  
TIMEOUT_APP_GEN = 1200       # 20 min - Full app generation
TIMEOUT_MODULE_GEN = 1800    # 30 min - Complex AIDL/C++ modules

def call_llm(
    prompt: str,
    system: str = "",
    *,
    stream: bool = False,
    temperature: float = 0.0,
    top_p: float = 1.0,
    stop: Optional[List[str]] = None,
    response_format: Optional[str] = None,
    timeout: Optional[int] = None,  # NEW: Optional timeout override
) -> str:
    """
    Call Ollama LLM with qwen2.5-coder:32b.
    
    Args:
        prompt: The prompt text
        system: System message (optional)
        stream: Enable streaming mode
        temperature: Sampling temperature (0.0 = deterministic)
        top_p: Nucleus sampling parameter
        stop: Stop sequences
        response_format: "json" for JSON mode
        timeout: Request timeout in seconds (default: DEFAULT_TIMEOUT=1800)
    
    Returns:
        The generated response text
    """
    # Use provided timeout or fall back to default
    actual_timeout = timeout if timeout is not None else DEFAULT_TIMEOUT
    
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
        resp = requests.post(OLLAMA_URL, json=payload, stream=stream, timeout=actual_timeout)
        resp.raise_for_status()
    except requests.exceptions.Timeout as e:
        raise TimeoutError(f"Ollama request timed out after {actual_timeout}s") from e
    except requests.exceptions.RequestException as e:
        raise RuntimeError(f"Ollama request failed: {e}") from e

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


def call_llm_json(
    prompt: str, 
    system: str = "", 
    max_retries: int = 6,
    timeout: Optional[int] = None  # NEW: Optional timeout override
) -> dict:
    """
    Reliable JSON extraction with cleaning + retries.
    Qwen2.5-Coder is excellent at JSON — this will almost always succeed on first try.
    
    Args:
        prompt: The prompt text
        system: System message (optional)
        max_retries: Maximum retry attempts
        timeout: Request timeout in seconds (default: DEFAULT_TIMEOUT)
    
    Returns:
        Parsed JSON dictionary
    """
    for attempt in range(max_retries):
        try:
            raw = call_llm(
                prompt=prompt,
                system=system,
                temperature=0.0,
                response_format="json",  # Enforced
                timeout=timeout,         # Pass through timeout
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


# === BACKWARD COMPATIBILITY ===
# Your existing code will work without changes!
# Old usage: call_llm(prompt) -> uses DEFAULT_TIMEOUT (1800s)
# New usage: call_llm(prompt, timeout=300) -> uses custom timeout (300s)

# === USAGE EXAMPLES ===
"""
# Example 1: Default timeout (30 minutes)
response = call_llm("Generate a large AIDL file")

# Example 2: Custom timeout (5 minutes) - for build glue
response = call_llm("Generate Android.bp", timeout=300)

# Example 3: JSON mode with custom timeout
data = call_llm_json("Return JSON config", timeout=180)
"""