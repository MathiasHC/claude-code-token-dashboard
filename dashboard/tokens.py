"""The access token: generating one, and matching one a human retyped.

The token is obscurity, not authentication — but it is obscurity somebody has
to copy onto a tablet by eye, with no clipboard between the two machines. The
original scheme, 22 characters of mixed-case base64url, is hostile to that: a
single wrong case gives a 404 indistinguishable from any other failure, and
iOS capitalises the first character of typed text unprompted.

So matching is deliberately forgiving of the ways transcription goes wrong,
and generation avoids the characters people confuse in the first place.

What is NOT forgiven: any difference in the actual characters. This widens
the target from one exact string to a small equivalence class around it, and
nothing else.
"""

from __future__ import annotations

import re
import secrets

#: Crockford's base32: no I, L, O or U. Removing I/L/O kills the 1/I/l and
#: 0/O confusions at the source; U is dropped in the original spec so that
#: random strings are less likely to spell something unfortunate.
ALPHABET = "0123456789ABCDEFGHJKMNPQRSTVWXYZ"

#: 16 symbols x 5 bits = 80 bits. Far beyond guessing on a home network, and
#: six characters shorter to type than what it replaces.
DEFAULT_LENGTH = 16

#: What people actually type when they meant the digit.
_CONFUSABLE = str.maketrans({"I": "1", "L": "1", "O": "0"})

_HYPHENS = re.compile(r"[-\s]")


def new_token() -> str:
    return "".join(secrets.choice(ALPHABET) for _ in range(DEFAULT_LENGTH))


def is_crockford(token: str) -> bool:
    """Whether a token can safely have I/L/O folded into 1/1/0.

    A token containing any of those characters *means* them — it predates
    this scheme — so folding would corrupt it. Tokens generated here never
    contain them, which makes the test both the detector and the guarantee.
    """
    return bool(token) and not (set(token.upper()) & {"I", "L", "O"})


def canonical(value: str, fold_confusable: bool) -> str:
    """Reduce a token to the form two transcriptions of it should share."""
    folded = _HYPHENS.sub("", value).upper()
    return folded.translate(_CONFUSABLE) if fold_confusable else folded


def matches(expected: str, candidate: str) -> bool:
    """Constant-time comparison of two tokens, modulo transcription.

    Normalisation happens first and is not constant-time, but it runs over a
    bounded, attacker-supplied string and leaks nothing about the secret. The
    comparison itself — the only step that touches `expected` — still is.
    """
    fold = is_crockford(expected)
    return secrets.compare_digest(
        canonical(candidate, fold).encode("utf-8", "surrogateescape"),
        canonical(expected, fold).encode("utf-8"),
    )
