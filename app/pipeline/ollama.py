"""Make sure the Ollama model is on this machine: OLLAMA_AUTO_PULL=true pulls it when it is missing.

A pull (~3 GB for gemma3:4b) runs in a background thread; calls made meanwhile go to the fallback (Together).
"""
import logging
import threading

import httpx

from app.config import get_settings

log = logging.getLogger(__name__)

_pulling = threading.Lock()


def has_model() -> bool:
    """True if OLLAMA_MODEL is pulled. Raises httpx.HTTPError if Ollama is not reachable."""
    s = get_settings()
    tags = httpx.get(f"{s.ollama_url.rstrip('/')}/api/tags", timeout=3).json()
    names = {m["name"] for m in tags.get("models", [])}
    return s.ollama_model in names or f"{s.ollama_model}:latest" in names


def is_pulling() -> bool:
    return _pulling.locked()


def ensure_model() -> bool:
    """Pull OLLAMA_MODEL if it is missing. Returns True when the model is available afterwards."""
    s = get_settings()
    if not _pulling.acquire(blocking=False):
        return False  # another thread is already pulling
    try:
        if has_model():
            return True
        log.info("ollama: %s not found, pulling it (this can take a few minutes)", s.ollama_model)
        r = httpx.post(f"{s.ollama_url.rstrip('/')}/api/pull", json={"model": s.ollama_model, "stream": False},
                       timeout=None)
        r.raise_for_status()
        log.info("ollama: pulled %s", s.ollama_model)
        return True
    except httpx.HTTPError as e:
        log.warning("ollama: cannot pull %s (%s); is Ollama installed and running?", s.ollama_model, e)
        return False
    finally:
        _pulling.release()


def ensure_model_in_background() -> None:
    if get_settings().ollama_auto_pull and not is_pulling():
        threading.Thread(target=ensure_model, name="ollama-pull", daemon=True).start()
