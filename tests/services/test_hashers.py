from app.services.hashers import (
    combine_hash_parts,
    fingerprint_tokens,
    normalize_text_for_fingerprint,
    token_similarity,
)


def test_normalize_text_for_fingerprint_collapses_noise() -> None:
    assert normalize_text_for_fingerprint("  Lager-Mitarbeiter, Berlin!  ") == "lager mitarbeiter berlin"


def test_combine_hash_parts_is_stable_for_whitespace_variants() -> None:
    assert combine_hash_parts("Lager Mitarbeiter", "Berlin") == combine_hash_parts("lager   mitarbeiter", " berlin ")


def test_token_similarity_uses_jaccard_logic() -> None:
    left = fingerprint_tokens("lager mitarbeiter produktion")
    right = fingerprint_tokens("lager produktion")

    assert token_similarity(left, right) == 2 / 3
