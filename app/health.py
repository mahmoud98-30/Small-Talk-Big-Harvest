"""Deep health check: which model backends are reachable right now."""
import httpx

from app.config import get_settings


def _together() -> dict:
    s = get_settings()
    if not s.together_api_key:
        return {"ok": False, "error": "no API key"}
    try:
        r = httpx.get(f"{s.together_url.rstrip('/')}/models",
                      headers={"Authorization": f"Bearer {s.together_api_key}"}, timeout=5)
        return {"ok": r.status_code == 200, "status": r.status_code}
    except httpx.HTTPError as e:
        return {"ok": False, "error": type(e).__name__}


def _ollama() -> dict:
    from app.pipeline import ollama

    s = get_settings()
    try:
        if ollama.has_model():
            return {"ok": True, "model": s.ollama_model}
        return {"ok": False, "model": s.ollama_model,
                "error": "pulling model" if ollama.is_pulling() else "model not pulled"}
    except Exception as e:
        return {"ok": False, "error": type(e).__name__}


def _local_whisper() -> dict:
    from app.pipeline import stt

    s = get_settings()
    if stt._model is not None:
        return {"ok": True, "model": s.whisper_model, "loaded": True}
    try:
        from faster_whisper.utils import download_model

        download_model(s.whisper_model, local_files_only=True)
        return {"ok": True, "model": s.whisper_model, "loaded": False}
    except Exception:
        return {"ok": False, "model": s.whisper_model, "error": "model not downloaded"}


def deep_health() -> dict:
    s = get_settings()
    t = _together()
    return {
        "stt": {"chain": [s.stt_provider, s.stt_fallback], "together": t, "local": _local_whisper()},
        "llm": {"chain": [s.llm_provider, s.llm_fallback], "together": t, "ollama": _ollama()},
    }
