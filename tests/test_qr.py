"""The QR encoder behind the startup banner.

A QR code that is subtly wrong still looks exactly like a QR code, so these
check decodability by construction rather than appearance: the Reed-Solomon
against the ISO/IEC 18004 published vector, and the structural invariants a
reader depends on. The end-to-end proof — feeding the output to a real
decoder — was done in a browser during development; see the module docstring.
"""

from __future__ import annotations

import pytest

from dashboard import qr

URL = "http://192.168.1.70:8420/d/8K3QW7ZR4NPT2VXY"


def test_reed_solomon_matches_the_published_vector():
    """ISO/IEC 18004 worked example. If this drifts, every code is corrupt
    in a way no amount of looking at it would reveal."""
    data = [32, 91, 11, 120, 209, 114, 220, 77, 67, 64, 236, 17, 236, 17, 236, 17]
    assert qr._ec_codewords(data, 10) == [196, 35, 39, 119, 235, 215, 231, 226, 93, 23]


def test_the_matrix_is_square_and_a_legal_size():
    matrix = qr.encode(URL)
    size = len(matrix)
    assert all(len(row) == size for row in matrix)
    assert (size - 17) % 4 == 0, "size must be 4 x version + 17"
    assert 21 <= size <= 57


def test_every_module_is_decided():
    """A None left in the matrix means a cell the placement logic missed."""
    assert all(cell in (0, 1) for row in qr.encode(URL) for cell in row)


def test_the_three_finder_patterns_are_present():
    """A reader locates the code by these. Without all three it is not a QR
    code at all, whatever it looks like."""
    matrix = qr.encode(URL)
    size = len(matrix)
    for row, col in ((0, 0), (0, size - 7), (size - 7, 0)):
        assert all(matrix[row][col + i] == 1 for i in range(7))
        assert matrix[row + 1][col + 1] == 0
        assert matrix[row + 3][col + 3] == 1


def test_the_timing_patterns_alternate():
    matrix = qr.encode(URL)
    size = len(matrix)
    for i in range(8, size - 8):
        assert matrix[6][i] == (1 if i % 2 == 0 else 0)
        assert matrix[i][6] == (1 if i % 2 == 0 else 0)


def test_the_dark_module_is_set():
    matrix = qr.encode(URL)
    assert matrix[len(matrix) - 8][8] == 1


def test_a_longer_payload_needs_a_larger_matrix():
    assert len(qr.encode("A" * 100)) > len(qr.encode("A"))


def test_a_payload_beyond_this_encoder_says_so():
    """Better an explicit error at startup than a silently truncated URL."""
    with pytest.raises(ValueError, match="too long"):
        qr.encode("A" * 3000)


def test_render_includes_the_required_quiet_zone():
    """Four modules of margin is a spec requirement, and readers genuinely
    fail without it."""
    rendered = qr.render(URL).splitlines()
    size = len(qr.encode(URL))
    assert len(rendered) == size + 8
    assert rendered[0].strip() == "", "top quiet zone is not blank"
    assert rendered[-1].strip() == "", "bottom quiet zone is not blank"


def test_ansi_render_paints_both_polarities():
    """Block characters can only guess at the terminal's background; a code
    rendered the wrong way round is unscannable. ANSI states both."""
    rendered = qr.render(URL, ansi=True)
    assert "\033[40m" in rendered
    assert "\033[47m" in rendered
