"""A minimal QR encoder, so the startup banner can show a scannable URL.

Written out rather than pulled in because this project has no runtime
dependencies and is not about to acquire one for a banner. Scope is cut to
exactly what that banner needs: byte mode, error-correction level M,
versions 1-10, rendered as text.

The output is verified against the published test vector in ISO/IEC 18004
rather than by eye — a QR code that is subtly wrong still looks like a QR
code, so "it renders" proves nothing.
"""

from __future__ import annotations

# --- Galois field arithmetic (GF(256), x^8 + x^4 + x^3 + x^2 + 1) --------

_EXP = [0] * 512
_LOG = [0] * 256


def _init_tables() -> None:
    x = 1
    for i in range(255):
        _EXP[i] = x
        _LOG[x] = i
        x <<= 1
        if x & 0x100:
            x ^= 0x11D
    for i in range(255, 512):
        _EXP[i] = _EXP[i - 255]


_init_tables()


def _mul(a: int, b: int) -> int:
    if a == 0 or b == 0:
        return 0
    return _EXP[_LOG[a] + _LOG[b]]


def _generator(degree: int) -> list[int]:
    poly = [1]
    for i in range(degree):
        nxt = [0] * (len(poly) + 1)
        for j, coeff in enumerate(poly):
            nxt[j] ^= coeff
            nxt[j + 1] ^= _mul(coeff, _EXP[i])
        poly = nxt
    return poly


def _ec_codewords(data: list[int], count: int) -> list[int]:
    gen = _generator(count)
    remainder = list(data) + [0] * count
    for i in range(len(data)):
        factor = remainder[i]
        if factor == 0:
            continue
        for j, g in enumerate(gen):
            remainder[i + j] ^= _mul(g, factor)
    return remainder[len(data):]


# --- Version tables, level M only ---------------------------------------
# (total codewords, ec codewords per block, block counts) per version 1-10.
# group1_blocks, group1_data, group2_blocks, group2_data
_VERSIONS_M = {
    1: (26, 10, 1, 16, 0, 0),
    2: (44, 16, 1, 28, 0, 0),
    3: (70, 26, 1, 44, 0, 0),
    4: (100, 18, 2, 32, 0, 0),
    5: (134, 24, 2, 43, 0, 0),
    6: (172, 16, 4, 27, 0, 0),
    7: (196, 18, 4, 31, 0, 0),
    8: (242, 22, 2, 38, 2, 39),
    9: (292, 22, 3, 36, 2, 37),
    10: (346, 26, 4, 43, 1, 44),
}

_ALIGNMENT = {
    1: [], 2: [6, 18], 3: [6, 22], 4: [6, 26], 5: [6, 30],
    6: [6, 34], 7: [6, 22, 38], 8: [6, 24, 42], 9: [6, 26, 46], 10: [6, 28, 50],
}

#: Pre-computed BCH format strings for level M (bits 00) and masks 0-7.
_FORMAT_M = [
    0x5412, 0x5125, 0x5E7C, 0x5B4B, 0x45F9, 0x40CE, 0x4F97, 0x4AA0,
]

#: BCH version information, versions 7-10 (versions below 7 carry none).
_VERSION_INFO = {7: 0x07C94, 8: 0x085BC, 9: 0x09A99, 10: 0x0A4D3}


def _capacity(version: int) -> int:
    _, ec, b1, d1, b2, d2 = _VERSIONS_M[version]
    return b1 * d1 + b2 * d2


def _choose_version(length: int) -> int:
    for version in sorted(_VERSIONS_M):
        # 4 bits mode + 8 or 16 bits length + payload
        header = 4 + (8 if version < 10 else 16)
        if (header + length * 8 + 7) // 8 <= _capacity(version):
            return version
    raise ValueError("payload too long for this encoder (max version 10)")


def _encode_data(payload: bytes, version: int) -> list[int]:
    count_bits = 8 if version < 10 else 16
    bits: list[int] = []

    def put(value: int, width: int) -> None:
        for i in range(width - 1, -1, -1):
            bits.append((value >> i) & 1)

    put(0b0100, 4)                       # byte mode
    put(len(payload), count_bits)
    for byte in payload:
        put(byte, 8)

    capacity_bits = _capacity(version) * 8
    put(0, min(4, capacity_bits - len(bits)))          # terminator
    while len(bits) % 8:
        bits.append(0)

    codewords = [int("".join(str(b) for b in bits[i:i + 8]), 2) for i in range(0, len(bits), 8)]
    for pad in _cycle_pad(_capacity(version) - len(codewords)):
        codewords.append(pad)
    return codewords


def _cycle_pad(count: int) -> list[int]:
    return [0xEC if i % 2 == 0 else 0x11 for i in range(count)]


def _interleave(codewords: list[int], version: int) -> list[int]:
    _, ec_per_block, b1, d1, b2, d2 = _VERSIONS_M[version]
    blocks: list[list[int]] = []
    at = 0
    for _ in range(b1):
        blocks.append(codewords[at:at + d1])
        at += d1
    for _ in range(b2):
        blocks.append(codewords[at:at + d2])
        at += d2

    ec_blocks = [_ec_codewords(block, ec_per_block) for block in blocks]

    out: list[int] = []
    for i in range(max(len(b) for b in blocks)):
        for block in blocks:
            if i < len(block):
                out.append(block[i])
    for i in range(ec_per_block):
        for block in ec_blocks:
            out.append(block[i])
    return out


# --- Matrix construction -------------------------------------------------

def _new_matrix(size: int):
    return [[None] * size for _ in range(size)]


def _place_function_patterns(m, version: int) -> None:
    size = len(m)

    def finder(row: int, col: int) -> None:
        for r in range(-1, 8):
            for c in range(-1, 8):
                rr, cc = row + r, col + c
                if not (0 <= rr < size and 0 <= cc < size):
                    continue
                inside = 0 <= r < 7 and 0 <= c < 7
                ring = r in (0, 6) or c in (0, 6) or (2 <= r <= 4 and 2 <= c <= 4)
                m[rr][cc] = 1 if (inside and ring) else 0

    finder(0, 0)
    finder(0, size - 7)
    finder(size - 7, 0)

    for i in range(8, size - 8):                       # timing patterns
        bit = 1 if i % 2 == 0 else 0
        m[6][i] = bit
        m[i][6] = bit

    centres = _ALIGNMENT[version]
    for r in centres:
        for c in centres:
            if (r < 8 and c < 8) or (r < 8 and c > size - 9) or (r > size - 9 and c < 8):
                continue
            for dr in range(-2, 3):
                for dc in range(-2, 3):
                    m[r + dr][c + dc] = 1 if max(abs(dr), abs(dc)) != 1 else 0

    m[size - 8][8] = 1                                  # dark module


def _reserve_format(m) -> set[tuple[int, int]]:
    size = len(m)
    reserved = set()
    for i in range(9):
        reserved.add((8, i))
        reserved.add((i, 8))
    for i in range(8):
        reserved.add((8, size - 1 - i))
        reserved.add((size - 1 - i, 8))
    if len(m) >= 45:                                    # version >= 7
        for i in range(6):
            for j in range(3):
                reserved.add((size - 11 + j, i))
                reserved.add((i, size - 11 + j))
    return reserved


def _place_data(m, data: list[int], reserved: set[tuple[int, int]]) -> None:
    size = len(m)
    bits = [(byte >> i) & 1 for byte in data for i in range(7, -1, -1)]
    index = 0
    upward = True
    col = size - 1
    while col > 0:
        if col == 6:
            col -= 1
        rows = range(size - 1, -1, -1) if upward else range(size)
        for row in rows:
            for c in (col, col - 1):
                if m[row][c] is not None or (row, c) in reserved:
                    continue
                m[row][c] = bits[index] if index < len(bits) else 0
                index += 1
        upward = not upward
        col -= 2


def _mask_condition(mask: int, row: int, col: int) -> bool:
    if mask == 0:
        return (row + col) % 2 == 0
    if mask == 1:
        return row % 2 == 0
    if mask == 2:
        return col % 3 == 0
    if mask == 3:
        return (row + col) % 3 == 0
    if mask == 4:
        return (row // 2 + col // 3) % 2 == 0
    if mask == 5:
        return (row * col) % 2 + (row * col) % 3 == 0
    if mask == 6:
        return ((row * col) % 2 + (row * col) % 3) % 2 == 0
    return ((row + col) % 2 + (row * col) % 3) % 2 == 0


def _penalty(m) -> int:
    size = len(m)
    score = 0
    for line in list(m) + [list(col) for col in zip(*m)]:
        run, prev = 1, line[0]
        for cell in line[1:]:
            if cell == prev:
                run += 1
            else:
                if run >= 5:
                    score += 3 + (run - 5)
                run, prev = 1, cell
        if run >= 5:
            score += 3 + (run - 5)
    for r in range(size - 1):
        for c in range(size - 1):
            block = {m[r][c], m[r][c + 1], m[r + 1][c], m[r + 1][c + 1]}
            if len(block) == 1:
                score += 3
    pattern = [1, 0, 1, 1, 1, 0, 1, 0, 0, 0, 0]
    for line in list(m) + [list(col) for col in zip(*m)]:
        for i in range(size - 10):
            if line[i:i + 11] == pattern or line[i:i + 11] == pattern[::-1]:
                score += 40
    dark = sum(sum(row) for row in m)
    percent = dark * 100 // (size * size)
    score += 10 * (abs(percent - 50) // 5)
    return score


def _apply_format(m, mask: int, reserved: set[tuple[int, int]]) -> None:
    size = len(m)
    bits = [(_FORMAT_M[mask] >> i) & 1 for i in range(14, -1, -1)]
    coords_a = [(8, 0), (8, 1), (8, 2), (8, 3), (8, 4), (8, 5), (8, 7), (8, 8),
                (7, 8), (5, 8), (4, 8), (3, 8), (2, 8), (1, 8), (0, 8)]
    for bit, (r, c) in zip(bits, coords_a):
        m[r][c] = bit
    coords_b = [(size - 1 - i, 8) for i in range(7)] + [(8, size - 8 + i) for i in range(8)]
    for bit, (r, c) in zip(bits, coords_b):
        m[r][c] = bit


def _apply_version_info(m, version: int) -> None:
    if version < 7:
        return
    size = len(m)
    bits = [(_VERSION_INFO[version] >> i) & 1 for i in range(17, -1, -1)]
    for i, bit in enumerate(reversed(bits)):
        r, c = divmod(i, 3)
        m[size - 11 + c][r] = bit
        m[r][size - 11 + c] = bit


def encode(text: str) -> list[list[int]]:
    """The QR matrix for `text`, as rows of 0/1."""
    payload = text.encode("utf-8")
    version = _choose_version(len(payload))
    size = version * 4 + 17

    codewords = _interleave(_encode_data(payload, version), version)

    best = None
    for mask in range(8):
        m = _new_matrix(size)
        _place_function_patterns(m, version)
        reserved = _reserve_format(m)
        _place_data(m, codewords, reserved)
        for r in range(size):
            for c in range(size):
                if (r, c) in reserved:
                    continue
                if m[r][c] is None:
                    m[r][c] = 0
        masked = [row[:] for row in m]
        function_cells = _function_cells(size, version, reserved)
        for r in range(size):
            for c in range(size):
                if (r, c) not in function_cells and _mask_condition(mask, r, c):
                    masked[r][c] ^= 1
        _apply_format(masked, mask, reserved)
        _apply_version_info(masked, version)
        score = _penalty(masked)
        if best is None or score < best[0]:
            best = (score, masked)
    return best[1]


def _function_cells(size: int, version: int, reserved: set[tuple[int, int]]) -> set[tuple[int, int]]:
    cells = set(reserved)
    for row, col in ((0, 0), (0, size - 7), (size - 7, 0)):
        for r in range(-1, 8):
            for c in range(-1, 8):
                if 0 <= row + r < size and 0 <= col + c < size:
                    cells.add((row + r, col + c))
    for i in range(size):
        cells.add((6, i))
        cells.add((i, 6))
    for r in _ALIGNMENT[version]:
        for c in _ALIGNMENT[version]:
            if (r < 8 and c < 8) or (r < 8 and c > size - 9) or (r > size - 9 and c < 8):
                continue
            for dr in range(-2, 3):
                for dc in range(-2, 3):
                    cells.add((r + dr, c + dc))
    cells.add((size - 8, 8))
    return cells


def render(text: str, ansi: bool = False, quiet_zone: int = 4) -> str:
    """The matrix as text.

    Polarity is the trap here. A terminal's background may be light or dark,
    and a code rendered with the wrong polarity is unscannable by most
    readers. Block characters can only guess. So the banner uses `ansi`,
    which paints each module's background explicitly and is correct on any
    theme; the block form exists for tests and for pasting into places that
    strip colour.

    The quiet zone is not decoration — the spec requires four modules of
    margin, and readers genuinely fail without it.
    """
    matrix = encode(text)
    size = len(matrix)
    width = size + quiet_zone * 2
    rows = [[0] * width for _ in range(quiet_zone)]
    rows += [[0] * quiet_zone + row + [0] * quiet_zone for row in matrix]
    rows += [[0] * width for _ in range(quiet_zone)]

    if ansi:
        dark, light = "\033[40m  \033[0m", "\033[47m  \033[0m"
    else:
        dark, light = "\u2588\u2588", "  "
    return "\n".join("".join(dark if cell else light for cell in row) for row in rows)
