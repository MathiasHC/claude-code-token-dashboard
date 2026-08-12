"""Generating an access token, and matching one somebody retyped."""

from __future__ import annotations

import pytest

from dashboard import tokens

LEGACY = "tp-5IZePLpxKjEtdmZYttA"     # base64url, as generated before
MODERN = "8K3QW7ZR4NPT2VXY"          # Crockford, as generated now


# --- generation ---------------------------------------------------------

def test_generated_tokens_avoid_the_confusable_letters():
    """I/L/O are the whole point: they are what 1 and 0 get mistaken for."""
    for _ in range(200):
        assert not (set(tokens.new_token()) & {"I", "L", "O", "U"})


def test_generated_tokens_are_uppercase_and_long_enough():
    token = tokens.new_token()
    assert token == token.upper()
    assert len(token) == tokens.DEFAULT_LENGTH
    assert set(token) <= set(tokens.ALPHABET)


def test_tokens_are_not_predictable():
    assert len({tokens.new_token() for _ in range(200)}) == 200


# --- matching a retyped token -------------------------------------------

def test_case_no_longer_matters():
    """The actual failure that started this: one character typed in the
    wrong case gave a 404 indistinguishable from any other."""
    assert tokens.matches(LEGACY, LEGACY)
    assert tokens.matches(LEGACY, LEGACY.lower())
    assert tokens.matches(LEGACY, LEGACY.upper())
    assert tokens.matches(LEGACY, "TP-5izePLPXKJETDMZYTTa")


def test_ios_style_autocapitalisation_is_forgiven():
    """iOS capitalises the first character of typed text unprompted."""
    assert tokens.matches(LEGACY, LEGACY[0].upper() + LEGACY[1:])


def test_confusable_characters_are_folded_for_modern_tokens():
    assert tokens.matches(MODERN, MODERN.replace("0", "O"))
    assert tokens.matches(MODERN, MODERN.replace("1", "I"))
    assert tokens.matches("8K3QW7ZR4NPT2VXY", "8k3qw7zr4npt2vxy")


def test_hyphens_and_spaces_are_ignored():
    """People break long strings up when copying them by eye."""
    assert tokens.matches(MODERN, "8K3Q-W7ZR-4NPT-2VXY")
    assert tokens.matches(MODERN, "8K3Q W7ZR 4NPT 2VXY")


def test_a_legacy_token_containing_I_or_L_is_never_folded():
    """The load-bearing case for is_crockford. LEGACY contains a real I and
    a real L; folding them to 1 would corrupt a token that still works, and
    would silently lock the user out of their own dashboard."""
    assert not tokens.is_crockford(LEGACY)
    assert not tokens.matches(LEGACY, LEGACY.replace("I", "1"))
    assert not tokens.matches(LEGACY, LEGACY.replace("L", "1"))


def test_wrong_tokens_are_still_wrong():
    for wrong in ("", "x", MODERN[:-1], MODERN + "A", MODERN.replace("8", "9"), "../../etc/passwd"):
        assert not tokens.matches(MODERN, wrong), wrong


def test_matching_does_not_crash_on_non_ascii():
    """A stray client can send anything on the request line."""
    assert not tokens.matches(MODERN, "tøkèn")
    assert not tokens.matches(MODERN, "\udcff")


@pytest.mark.parametrize("token", [LEGACY, MODERN])
def test_a_token_always_matches_itself(token):
    assert tokens.matches(token, token)
