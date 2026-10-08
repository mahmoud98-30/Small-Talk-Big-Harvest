import json

import pytest

from app.languages import symptoms
from app.pipeline import extract, lexicon


def test_negation_does_not_cross_clauses():
    # "नहीं" belongs to the first clause; the holes in the second clause are present
    assert lexicon.scan("धब्बे नहीं हैं, लेकिन बेरी में छेद हैं।", "hi")["small_hole_in_berry"] == 1


def test_no_lexicon_for_language_is_noop():
    assert lexicon.scan("anything", "xx") == {}


def test_merge_rules():
    llm = {"a": 0, "b": 1, "c": -1, "d": 1, "e": 0}
    kw = {"a": 1, "b": 1, "c": 1, "d": 0}
    assert lexicon.merge(llm, kw) == {"a": 1, "b": 1, "c": 0, "d": 0, "e": 0}


def test_extract_fills_missed_symptom_and_resolves_conflict(monkeypatch):
    from app.config import get_settings

    monkeypatch.setattr(get_settings(), "lexicon_check", "always")
    llm_out = {k: 0 for k in symptoms()} | {"leaves_falling": 1}  # LLM missed holes, invented falling
    monkeypatch.setattr(extract, "_chat", lambda m, s, p=None: json.dumps(llm_out))
    flags = extract.extract_parameters("बेरी में छेद हैं, और पत्तियाँ नहीं गिर रहीं।", "hi")
    assert flags["small_hole_in_berry"] == 1  # filled from keywords
    assert flags["leaves_falling"] == 0  # LLM yes vs keyword no -> unknown


def test_lexicon_can_be_disabled(monkeypatch):
    from app.config import get_settings

    monkeypatch.setattr(get_settings(), "lexicon_check", "off")
    monkeypatch.setattr(extract, "_chat", lambda m, s, p=None: json.dumps({k: 0 for k in symptoms()}))
    assert extract.extract_parameters("बेरी में छेद हैं।", "hi")["small_hole_in_berry"] == 0


def test_local_mode_applies_only_to_ollama(monkeypatch):
    from app.config import get_settings

    s = get_settings()
    monkeypatch.setattr(s, "lexicon_check", "local")
    monkeypatch.setattr(s, "llm_fallback", "ollama")
    empty = json.dumps({k: 0 for k in symptoms()})
    text = "बेरी में छेद हैं।"

    monkeypatch.setattr(extract, "_chat", lambda m, sc, p=None: empty)  # together answers
    assert extract.extract_parameters(text, "hi")["small_hole_in_berry"] == 0

    def together_down(m, sc, p=None):
        if p == "together":
            raise extract.ProviderUnavailable("down")
        return empty

    monkeypatch.setattr(extract, "_chat", together_down)  # local fallback answers
    flags = extract.extract_parameters(text, "hi")
    assert flags.provider == "ollama" and flags["small_hole_in_berry"] == 1


def test_require_support_drops_unconfirmed_yes():
    llm = {"poor_growth_stunted": 1, "leaves_yellowing": 1, "wilting": 0}
    kw = {"leaves_yellowing": 1, "wilting": 1}
    out = lexicon.merge(llm, kw, {"poor_growth_stunted", "leaves_yellowing", "wilting"})
    assert out == {"poor_growth_stunted": 0, "leaves_yellowing": 1, "wilting": 1}


def test_invented_symptom_from_local_model_becomes_unknown(monkeypatch):
    from app.config import get_settings

    s = get_settings()
    monkeypatch.setattr(s, "lexicon_check", "local")
    monkeypatch.setattr(s, "llm_provider", "ollama")
    invented = {k: 0 for k in symptoms()} | {"leaves_yellowing": 1, "poor_growth_stunted": 1}
    monkeypatch.setattr(extract, "_chat", lambda m, sc, p=None: json.dumps(invented))
    flags = extract.extract_parameters("पेड़ मुरझा रहा है और पत्तियाँ पीली पड़ रही हैं।", "hi")
    assert flags["poor_growth_stunted"] == 0 and flags["wilting"] == 1 and flags["leaves_yellowing"] == 1


@pytest.mark.parametrize("text,expected", [
    ("मेरी पत्तियां पीली पड़ रही हैं।", {"leaves_yellowing": 1}),
    ("पीले धब्बे हैं पत्ते पर", {"yellow_spots_on_leaf": 1}),
    ("नीले डाग हैं पत्ते पर", {"yellow_spots_on_leaf": 1}),  # Whisper hears पीले as नीले
    ("नारंगी पाउडर नहीं है।", {"orange_powder_under_leaf": -1}),  # Hindi negator comes after
    ("बेरी में छेद नहीं।", {"small_hole_in_berry": -1}),
    ("पत्तियों पर कोई धब्बा नहीं", {"yellow_spots_on_leaf": -1, "brown_spot_grey_center": -1}),
    ("जड़ें काली और सड़ी हुई हैं।", {"dark_soft_roots": 1}),  # ड़ with a separate nukta
    ("बेरी में छोटे छेद हैं।", {"small_hole_in_berry": 1}),
    ("पत्तियाँ गिर रही हैं।", {"leaves_falling": 1}),
    ("पत्तियाँ गिर नहीं रहीं।", {"leaves_falling": -1}),
    ("पत्तियाँ नहीं गिर रहीं।", {"leaves_falling": -1}),  # negator inside the match
    ("बेरी नहीं गिर रहीं।", {"berries_falling_early": -1}),
    ("पौधा नहीं बढ़ रहा।", {"poor_growth_stunted": 1}),  # "not growing" is the symptom itself
    ("पत्तियों पर धब्बे नहीं हैं और पत्तियाँ गिर रही हैं।", {"leaves_falling": 1}),
    ("बेरी गिर रही हैं।", {"berries_falling_early": 1}),
    ("पेड़ मुरझा रहा है।", {"wilting": 1}),
    ("हरे कीड़े हैं और पत्तियाँ चिपचिपी हैं।", {"green_insects_on_leaves_stems": 1, "sticky_leaves": 1}),
    ("नमस्ते, मेरा एक सवाल है।", {}),
])
def test_scan_hindi(text, expected):
    assert lexicon.scan(text, "hi") == expected
