"""Twilio voice IVR (Hindi only, no menu): welcome -> record -> background pipeline -> poll -> answer."""
import logging

import httpx
from fastapi import APIRouter, BackgroundTasks, Depends, Query
from sqlalchemy import delete
from twilio.twiml.voice_response import VoiceResponse

from app.cases import create_case
from app.config import get_settings
from app.db import SessionLocal
from app.languages import default_language
from app.models import Case
from app.pipeline.run import run_case
from app.twilio_util import speak, twiml, validate_twilio

log = logging.getLogger(__name__)
router = APIRouter(prefix="/voice")

DIGITS = "0123456789*#"
DONE = {"answered", "in_review", "labeled", "error"}


@router.post("/incoming")
def incoming(form: dict = Depends(validate_twilio)):
    with SessionLocal() as session:
        case = create_case(session, "voice", default_language(), phone=form.get("From") or None,
                           audio_consent="answer_only")
    resp = VoiceResponse()
    # No language menu and no consent question: welcome, then straight to the recording; audio stays
    # "answer_only" (deleted after the answer). Key 9 during the greeting deletes the caller's data.
    g = resp.gather(num_digits=1, action=f"/voice/key?case_id={case.id}", timeout=1)
    speak(g, "prompt_welcome", case.language)
    _ask_describe(resp, case.id, case.language)
    return twiml(resp)


@router.post("/key")
def key(case_id: str, form: dict = Depends(validate_twilio)):
    digit = str(form.get("Digits", ""))
    resp = VoiceResponse()
    with SessionLocal() as session:
        case = session.get(Case, case_id)
        if case is None:
            resp.hangup()
            return twiml(resp)
        if digit == "9" and case.phone_hash:  # delete-my-data
            session.execute(delete(Case).where(Case.phone_hash == case.phone_hash))
            session.commit()
            speak(resp, "prompt_data_deleted", case.language)
            resp.hangup()
            return twiml(resp)
        lang = case.language
    _ask_describe(resp, case_id, lang)  # any other key: carry on to the recording
    return twiml(resp)


def _ask_describe(resp: VoiceResponse, case_id: str, lang: str) -> None:
    speak(resp, "prompt_describe", lang)
    resp.record(max_length=30, finish_on_key=DIGITS, play_beep=True, timeout=5,
                action=f"/voice/recorded?case_id={case_id}")
    # Twilio continues here if nothing was recorded
    resp.redirect(f"/voice/result?case_id={case_id}&try={get_settings().voice_max_tries}")


def _download_and_store(case_id: str, url: str, sid: str | None) -> None:
    s = get_settings()
    auth = (s.twilio_account_sid, s.twilio_auth_token) if s.twilio_account_sid else None
    r = httpx.get(url + ".wav", auth=auth, timeout=30, follow_redirects=True)
    r.raise_for_status()
    from app.audio import save_upload

    key = save_upload(r.content, ".wav")
    with SessionLocal() as session:
        case = session.get(Case, case_id)
        case.audio_path = key
        session.commit()
    if sid and s.twilio_account_sid:
        try:
            from twilio.rest import Client

            Client(s.twilio_account_sid, s.twilio_auth_token).recordings(sid).delete()
        except Exception:
            log.warning("could not delete Twilio recording for case %s", case_id, exc_info=True)


def process_recording(case_id: str, url: str, sid: str | None) -> None:
    try:
        _download_and_store(case_id, url, sid)
    except Exception:
        log.exception("case %s: recording download failed", case_id)
        with SessionLocal() as session:
            case = session.get(Case, case_id)
            case.status, case.result, case.not_sure_reason = "error", "not_sure", "extraction_failed"
            session.commit()
        return
    run_case(case_id)


@router.post("/recorded")
def recorded(case_id: str, background: BackgroundTasks, form: dict = Depends(validate_twilio)):
    url = form.get("RecordingUrl")
    resp = VoiceResponse()
    with SessionLocal() as session:
        case = session.get(Case, case_id)
        if case is None:
            resp.hangup()
            return twiml(resp)
        lang = case.language
    if not url:
        resp.redirect(f"/voice/result?case_id={case_id}&try={get_settings().voice_max_tries}")
        return twiml(resp)
    background.add_task(process_recording, case_id, url, form.get("RecordingSid"))
    speak(resp, "prompt_wait", lang)
    resp.redirect(f"/voice/result?case_id={case_id}&try=1")
    return twiml(resp)


@router.post("/result")
def result(case_id: str, n: int = Query(1, alias="try"), form: dict = Depends(validate_twilio)):
    resp = VoiceResponse()
    with SessionLocal() as session:
        case = session.get(Case, case_id)
        if case is None:
            resp.hangup()
            return twiml(resp)
        lang = case.language
        if case.status in DONE:
            speak(resp, case.result or "not_sure", lang)
        elif n < get_settings().voice_max_tries:
            resp.pause(length=3)
            resp.redirect(f"/voice/result?case_id={case_id}&try={n + 1}")
            return twiml(resp)
        else:
            case.status, case.result = "in_review", "not_sure"
            case.not_sure_reason = case.not_sure_reason or "timeout"
            session.commit()
            speak(resp, "not_sure", lang)
    speak(resp, "goodbye", lang)
    resp.hangup()
    return twiml(resp)
