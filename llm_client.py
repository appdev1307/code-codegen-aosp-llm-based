# FILE: llm_client.py

import json
import os
import re
from typing import Any, Dict, List, Optional

import requests

# -----------------------------------------------------------------------------
# CONFIG (defaults keep your current behavior)
# -----------------------------------------------------------------------------
OLLAMA_HOST = os.getenv("OLLAMA_HOST", "http://127.0.0.1:11434").rstrip("/")
MODEL = os.getenv("OLLAMA_MODEL", "qwen2.5-coder:7b")
TIMEOUT = int(os.getenv("OLLAMA_TIMEOUT", "600"))  # seconds

# You were using /api/generate. Keep it as primary, but add a chat fallback.
OLLAMA_GENERATE_URL = f"{OLLAMA_HOST}/api/generate"
OLLAMA_CHAT_URL = f"{OLLAMA_HOST}/api/chat"

# -----------------------------------------------------------------------------
# HELPERS
# -----------------------------------------------------------------------------
_FENCE_RE = re.compile(r"^\s*```(?:json)?\s*|\s*```\s*$", re.IGNORECASE | re.MULTILINE)

def _strip_code_fences(text: str) -> str:
    if not text:
        return ""
    t = text.strip()
    if "```" in t:
        t = _FENCE_RE.sub("", t).strip()
    return t

def _looks_like_tool_placeholder(text: str) -> bool:
    """
    Defensive detection: some stacks accidentally return tool-calling placeholders.
    We treat these as invalid for codegen and fall back to /api/chat once.
    """
    t = (text or "").strip()
    if not t:
        return True
    # Exact placeholder you showed
    if t == '{"response": "JSON data has been returned."}':
        return True
    if t.lower() == "json data has been returned.":
        return True
    # Often appears when something upstream swallowed the actual content
    if len(t) < 80 and "json data has been returned" in t.lower():
        return True
    return False

def _call_generate(
    prompt: str,
    system: str,
    *,
    stream: bool,
    temperature: float,
    top_p: float,
    stop: Optional[List[str]],
    response_format: Optional[str],
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
    # Only include "format" if explicitly requested.
    # NOTE: Ollama supports "json" for some models; for others it can error.
    if response_format is not None:
        payload["format"] = response_format

    resp = requests.post(OLLAMA_GENERATE_URL, json=payload, stream=stream, timeout=TIMEOUT)
    resp.raise_for_status()

    if not stream:
        data = resp.json()
        return (data.get("response", "") or "").strip()

    chunks: List[str] = []
    for line in resp.iter_lines(decode_unicode=True):
        if not line:
            continue
        data = json.loads(line)
        if "response" in data:
            chunks.append(data["response"])
        if data.get("done"):
            break
    return "".join(chunks).strip()

def _call_chat(
    prompt: str,
    system: str,
    *,
    stream: bool,
    temperature: float,
    top_p: float,
    stop: Optional[List[str]],
    response_format: Optional[str],
) -> str:
    """
    /api/chat tends to behave better for “role” separated system/user prompts.
    Ollama's chat endpoint returns:
      {"message":{"role":"assistant","content":"..."},"done":true,...}
    """
    messages: List[Dict[str, str]] = []
    if system:
        messages.append({"role": "system", "content": system})
    messages.append({"role": "user", "content": prompt})

    payload: Dict[str, Any] = {
        "model": MODEL,
        "messages": messages,
        "stream": stream,
        "options": {
            "temperature": temperature,
            "top_p": top_p,
        },
    }
    if stop:
        payload["options"]["stop"] = stop
    # Ollama chat also supports format for some models
    if response_format is not None:
        payload["format"] = response_format

    resp = requests.post(OLLAMA_CHAT_URL, json=payload, stream=stream, timeout=TIMEOUT)
    resp.raise_for_status()

    if not stream:
        data = resp.json()
        msg = data.get("message") or {}
        return (msg.get("content", "") or "").strip()

    chunks: List[str] = []
    for line in resp.iter_lines(decode_unicode=True):
        if not line:
            continue
        data = json.loads(line)
        msg = data.get("message") or {}
        if "content" in msg:
            chunks.append(msg["content"])
        if data.get("done"):
            break
    return "".join(chunks).strip()

# -----------------------------------------------------------------------------
# PUBLIC API
# -----------------------------------------------------------------------------
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
    Ollama client for Qwen.

    Key improvements:
    - Keeps your /api/generate as primary (no breaking change)
    - Strips accidental ``` fences from model output (common for JSON)
    - If the output looks like the placeholder you showed, auto-fallback once to /api/chat
      (this fixes the “JSON data has been returned.” symptom without changing your agents)
    """

    # Primary: generate
    out = _call_generate(
        prompt,
        system,
        stream=stream,
        temperature=temperature,
        top_p=top_p,
        stop=stop,
        response_format=response_format,
    )
    out = _strip_code_fences(out)

    # If generate gives the placeholder/empty, try chat once (same prompt/system)
    if _looks_like_tool_placeholder(out):
        out2 = _call_chat(
            prompt,
            system,
            stream=stream,
            temperature=temperature,
            top_p=top_p,
            stop=stop,
            response_format=response_format,
        )
        out2 = _strip_code_fences(out2)
        # Prefer chat if it produced something meaningful
        if out2 and not _looks_like_tool_placeholder(out2):
            return out2

    return out
