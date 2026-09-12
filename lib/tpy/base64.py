# base64 -- RFC 4648 binary-to-text encodings (b64, b32, b16).
#
# Gaps vs. CPython:
#   - b85/a85 encodings not implemented (rare; separate algorithms).
#   - memoryview input not accepted: depends on TPy builtin `memoryview`,
#     which is itself missing (see STDLIB_ROADMAP.md builtins section).
#   - `b64decode(..., validate=False)` is stricter than CPython on
#     degenerate padding. CPython's `binascii.a2b_base64` silently eats
#     excess padding bytes: `b64decode(b"====") == b""`,
#     `b64decode(b"TWFu=") == b"Man"`. This implementation raises
#     ValueError in those cases (padding-length guard is unconditional).
#     Valid inputs decode identically; only pathological inputs differ.
#
# Performance TODO -- benchmark vs. optimal C and close the gap.
# Current release-build numbers (1 MB payload, 50-iter avg, debug build
# excluded): TPy encode 540 MB/s, decode 500 MB/s; CPython `binascii`
# (hand-tuned C) encode 720 MB/s, decode 740 MB/s -- roughly 1.3-1.5x
# slower. Small-payload (32 B token) decode already beats CPython
# (lower per-call overhead). Low-hanging fruit to close the bulk gap:
#   1. Elide bounds checks on `data[i]`/`data[i+1]`/... when the loop
#      guard (`i + 3 <= n`) proves in-range. Needs value-range analysis
#      to flow across the if/while. Generic compiler work; helps every
#      stdlib byte-level loop. Tracked in TODO.md.
#   2. Pre-size the output bytearray to the known final length instead
#      of growing via push_back. CPython's `bytearray` has no `reserve`,
#      so this needs an internal capacity-hint primitive (a public
#      `bytearray.reserve()` would diverge from CPython) -- deferred.
#   3. Fuse the `_filter_b64_input` + `_b64_decode` passes in lax mode
#      (validate=False) -- single walk, skip non-alphabet chars as
#      encountered.
#   4. `encodebytes`: inline the 76-char line wrap into the encode loop
#      instead of encoding once and re-walking to insert `\n`.
# Compare against CPython on representative payloads before claiming a
# fix works -- aim for parity with binascii on 1 MB, superior on small
# inputs.
# tpy: cpp_namespace("tpystd::base64")
from tpy import int32, uint8, dispatch

_B64_STD: bytes = b"ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghijklmnopqrstuvwxyz0123456789+/"
_B64_URL: bytes = b"ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghijklmnopqrstuvwxyz0123456789-_"
_B32_ALPHA: bytes = b"ABCDEFGHIJKLMNOPQRSTUVWXYZ234567"
_B16_ALPHA: bytes = b"0123456789ABCDEF"

_PAD: int32 = 61  # '='
_CHAR_PLUS: int32 = 43
_CHAR_SLASH: int32 = 47
_CHAR_MINUS: int32 = 45
_CHAR_UNDER: int32 = 95

def _b64_encode(data: bytes, alphabet: bytes) -> bytes:
    n: int32 = int32(len(data))
    result: bytearray = bytearray()
    i: int32 = 0
    while i + 3 <= n:
        b0: int32 = int32(data[i])
        b1: int32 = int32(data[i + 1])
        b2: int32 = int32(data[i + 2])
        result.append(alphabet[b0 >> 2])
        result.append(alphabet[((b0 & 0x03) << 4) | (b1 >> 4)])
        result.append(alphabet[((b1 & 0x0F) << 2) | (b2 >> 6)])
        result.append(alphabet[b2 & 0x3F])
        i += 3
    rem: int32 = n - i
    if rem == 1:
        r0: int32 = int32(data[i])
        result.append(alphabet[r0 >> 2])
        result.append(alphabet[(r0 & 0x03) << 4])
        result.append(uint8(_PAD))
        result.append(uint8(_PAD))
    elif rem == 2:
        s0: int32 = int32(data[i])
        s1: int32 = int32(data[i + 1])
        result.append(alphabet[s0 >> 2])
        result.append(alphabet[((s0 & 0x03) << 4) | (s1 >> 4)])
        result.append(alphabet[(s1 & 0x0F) << 2])
        result.append(uint8(_PAD))
    return bytes(result)

def _b64_char_to_value(c: int32, c62: int32, c63: int32) -> int32:
    if c >= 65 and c <= 90:
        return c - 65
    if c >= 97 and c <= 122:
        return c - 97 + 26
    if c >= 48 and c <= 57:
        return c - 48 + 52
    if c == c62:
        return 62
    if c == c63:
        return 63
    raise ValueError("Invalid base64 character")

def _b64_decode(data: bytes, c62: int32, c63: int32) -> bytes:
    n: int32 = int32(len(data))
    if n % 4 != 0:
        raise ValueError("Invalid base64-encoded data length")
    result: bytearray = bytearray()
    i: int32 = 0
    while i < n:
        c0: int32 = int32(data[i])
        c1: int32 = int32(data[i + 1])
        c2: int32 = int32(data[i + 2])
        c3: int32 = int32(data[i + 3])
        d0: int32 = _b64_char_to_value(c0, c62, c63)
        d1: int32 = _b64_char_to_value(c1, c62, c63)
        if c2 == _PAD:
            if c3 != _PAD or i + 4 != n:
                raise ValueError("Invalid base64 padding")
            result.append(uint8(((d0 << 2) | (d1 >> 4)) & 0xFF))
        elif c3 == _PAD:
            if i + 4 != n:
                raise ValueError("Invalid base64 padding")
            e2: int32 = _b64_char_to_value(c2, c62, c63)
            result.append(uint8(((d0 << 2) | (d1 >> 4)) & 0xFF))
            result.append(uint8((((d1 & 0x0F) << 4) | (e2 >> 2)) & 0xFF))
        else:
            f2: int32 = _b64_char_to_value(c2, c62, c63)
            f3: int32 = _b64_char_to_value(c3, c62, c63)
            result.append(uint8(((d0 << 2) | (d1 >> 4)) & 0xFF))
            result.append(uint8((((d1 & 0x0F) << 4) | (f2 >> 2)) & 0xFF))
            result.append(uint8((((f2 & 0x03) << 6) | f3) & 0xFF))
        i += 4
    return bytes(result)

def _build_altchars_alphabet(altchars: bytes) -> bytes:
    if len(altchars) != 2:
        raise ValueError("altchars must be 2 bytes")
    buf: bytearray = bytearray()
    i: int32 = 0
    while i < 62:
        buf.append(_B64_STD[i])
        i += 1
    buf.append(altchars[0])
    buf.append(altchars[1])
    return bytes(buf)

def _filter_b64_input(data: bytes, c62: int32, c63: int32) -> bytes:
    # CPython's validate=False default silently drops non-alphabet chars
    # (matching RFC 4648's MIME-mode leniency). Keep padding chars.
    buf: bytearray = bytearray()
    n: int32 = int32(len(data))
    i: int32 = 0
    while i < n:
        c: int32 = int32(data[i])
        keep: bool = False
        if c >= 65 and c <= 90:
            keep = True
        elif c >= 97 and c <= 122:
            keep = True
        elif c >= 48 and c <= 57:
            keep = True
        elif c == c62 or c == c63 or c == _PAD:
            keep = True
        if keep:
            buf.append(uint8(c))
        i += 1
    return bytes(buf)

def b64encode(data: bytes, altchars: bytes | None = None) -> bytes:
    if altchars is None:
        return _b64_encode(data, _B64_STD)
    return _b64_encode(data, _build_altchars_alphabet(altchars))

@dispatch
def b64decode(data: bytes, altchars: bytes | None = None, validate: bool = False) -> bytes:
    c62: int32 = _CHAR_PLUS
    c63: int32 = _CHAR_SLASH
    if altchars is not None:
        if len(altchars) != 2:
            raise ValueError("altchars must be 2 bytes")
        c62 = int32(altchars[0])
        c63 = int32(altchars[1])
    if validate:
        return _b64_decode(data, c62, c63)
    return _b64_decode(_filter_b64_input(data, c62, c63), c62, c63)

@dispatch
def b64decode(data: str, altchars: bytes | None = None, validate: bool = False) -> bytes:
    return b64decode(data.encode(), altchars, validate)

def standard_b64encode(data: bytes) -> bytes:
    return _b64_encode(data, _B64_STD)

@dispatch
def standard_b64decode(data: bytes) -> bytes:
    return _b64_decode(_filter_b64_input(data, _CHAR_PLUS, _CHAR_SLASH), _CHAR_PLUS, _CHAR_SLASH)

@dispatch
def standard_b64decode(data: str) -> bytes:
    return standard_b64decode(data.encode())

def urlsafe_b64encode(data: bytes) -> bytes:
    return _b64_encode(data, _B64_URL)

@dispatch
def urlsafe_b64decode(data: bytes) -> bytes:
    return _b64_decode(_filter_b64_input(data, _CHAR_MINUS, _CHAR_UNDER), _CHAR_MINUS, _CHAR_UNDER)

@dispatch
def urlsafe_b64decode(data: str) -> bytes:
    return urlsafe_b64decode(data.encode())

def b16encode(data: bytes) -> bytes:
    n: int32 = int32(len(data))
    result: bytearray = bytearray()
    i: int32 = 0
    while i < n:
        b: int32 = int32(data[i])
        result.append(_B16_ALPHA[b >> 4])
        result.append(_B16_ALPHA[b & 0x0F])
        i += 1
    return bytes(result)

def _b16_char_to_value(c: int32, casefold: bool) -> int32:
    if c >= 48 and c <= 57:
        return c - 48
    if c >= 65 and c <= 70:
        return c - 65 + 10
    if casefold and c >= 97 and c <= 102:
        return c - 97 + 10
    raise ValueError("Invalid base16 character")

@dispatch
def b16decode(data: bytes, casefold: bool = False) -> bytes:
    n: int32 = int32(len(data))
    if n % 2 != 0:
        raise ValueError("Invalid base16-encoded data length")
    result: bytearray = bytearray()
    i: int32 = 0
    while i < n:
        hi: int32 = _b16_char_to_value(int32(data[i]), casefold)
        lo: int32 = _b16_char_to_value(int32(data[i + 1]), casefold)
        result.append(uint8(((hi << 4) | lo) & 0xFF))
        i += 2
    return bytes(result)

@dispatch
def b16decode(data: str, casefold: bool = False) -> bytes:
    return b16decode(data.encode(), casefold)

def b32encode(data: bytes) -> bytes:
    # 5 input bytes (40 bits) -> 8 output chars (5 bits each).
    n: int32 = int32(len(data))
    result: bytearray = bytearray()
    i: int32 = 0
    while i + 5 <= n:
        b0: int32 = int32(data[i])
        b1: int32 = int32(data[i + 1])
        b2: int32 = int32(data[i + 2])
        b3: int32 = int32(data[i + 3])
        b4: int32 = int32(data[i + 4])
        result.append(_B32_ALPHA[(b0 >> 3) & 0x1F])
        result.append(_B32_ALPHA[((b0 & 0x07) << 2) | (b1 >> 6)])
        result.append(_B32_ALPHA[(b1 >> 1) & 0x1F])
        result.append(_B32_ALPHA[((b1 & 0x01) << 4) | (b2 >> 4)])
        result.append(_B32_ALPHA[((b2 & 0x0F) << 1) | (b3 >> 7)])
        result.append(_B32_ALPHA[(b3 >> 2) & 0x1F])
        result.append(_B32_ALPHA[((b3 & 0x03) << 3) | (b4 >> 5)])
        result.append(_B32_ALPHA[b4 & 0x1F])
        i += 5
    rem: int32 = n - i
    if rem > 0:
        # CPython's b32encode pads input with zero bytes to 5-byte boundary,
        # emits 8 output chars, then replaces trailing positions with '=' by
        # rem-based count: 1->6 pads, 2->4, 3->3, 4->1.
        t0: int32 = int32(data[i])
        t1: int32 = 0
        t2: int32 = 0
        t3: int32 = 0
        if rem >= 2:
            t1 = int32(data[i + 1])
        if rem >= 3:
            t2 = int32(data[i + 2])
        if rem >= 4:
            t3 = int32(data[i + 3])
        result.append(_B32_ALPHA[(t0 >> 3) & 0x1F])
        result.append(_B32_ALPHA[((t0 & 0x07) << 2) | (t1 >> 6)])
        if rem == 1:
            result.append(uint8(_PAD))
            result.append(uint8(_PAD))
            result.append(uint8(_PAD))
            result.append(uint8(_PAD))
            result.append(uint8(_PAD))
            result.append(uint8(_PAD))
            return bytes(result)
        result.append(_B32_ALPHA[(t1 >> 1) & 0x1F])
        result.append(_B32_ALPHA[((t1 & 0x01) << 4) | (t2 >> 4)])
        if rem == 2:
            result.append(uint8(_PAD))
            result.append(uint8(_PAD))
            result.append(uint8(_PAD))
            result.append(uint8(_PAD))
            return bytes(result)
        result.append(_B32_ALPHA[((t2 & 0x0F) << 1) | (t3 >> 7)])
        if rem == 3:
            result.append(uint8(_PAD))
            result.append(uint8(_PAD))
            result.append(uint8(_PAD))
            return bytes(result)
        result.append(_B32_ALPHA[(t3 >> 2) & 0x1F])
        result.append(_B32_ALPHA[(t3 & 0x03) << 3])
        result.append(uint8(_PAD))
    return bytes(result)

def _b32_char_to_value(c: int32) -> int32:
    if c >= 65 and c <= 90:
        return c - 65
    if c >= 50 and c <= 55:  # '2'-'7'
        return c - 50 + 26
    raise ValueError("Invalid base32 character")

def _b32_preprocess(data: bytes, casefold: bool, map01: bytes | None) -> bytes:
    n: int32 = int32(len(data))
    buf: bytearray = bytearray()
    map_target: int32 = 0
    if map01 is not None:
        # RFC 4648 section 2.4 recommends 'I' or 'L' as targets, but CPython
        # accepts any single byte -- match that.
        if len(map01) != 1:
            raise ValueError("map01 must be a single byte")
        map_target = int32(map01[0])
    i: int32 = 0
    while i < n:
        c: int32 = int32(data[i])
        if casefold and c >= 97 and c <= 122:
            c = c - 32  # to upper
        if map01 is not None:
            if c == 48:  # '0' -> 'O'
                c = 79
            elif c == 49:  # '1' -> map_target
                c = map_target
        buf.append(uint8(c))
        i += 1
    return bytes(buf)

@dispatch
def b32decode(data: bytes, casefold: bool = False, map01: bytes | None = None) -> bytes:
    if casefold or map01 is not None:
        return _b32decode_impl(_b32_preprocess(data, casefold, map01))
    return _b32decode_impl(data)

@dispatch
def b32decode(data: str, casefold: bool = False, map01: bytes | None = None) -> bytes:
    return b32decode(data.encode(), casefold, map01)

def _b32decode_impl(data: bytes) -> bytes:
    n: int32 = int32(len(data))
    if n % 8 != 0:
        raise ValueError("Invalid base32-encoded data length")
    result: bytearray = bytearray()
    i: int32 = 0
    while i < n:
        # Count pads in this 8-char block (only trailing are valid).
        pad: int32 = 0
        j: int32 = 7
        while j >= 0 and int32(data[i + j]) == _PAD:
            pad += 1
            j -= 1
        if pad != 0 and pad != 1 and pad != 3 and pad != 4 and pad != 6:
            raise ValueError("Invalid base32 padding")
        if pad > 0 and i + 8 != n:
            raise ValueError("Invalid base32 padding")
        # Decode non-pad chars.
        vals: list[int32] = [0, 0, 0, 0, 0, 0, 0, 0]
        k: int32 = 0
        while k < 8 - pad:
            vals[k] = _b32_char_to_value(int32(data[i + k]))
            k += 1
        result.append(uint8(((vals[0] << 3) | (vals[1] >> 2)) & 0xFF))
        if pad == 6:
            i += 8
            continue
        result.append(uint8((((vals[1] & 0x03) << 6) | (vals[2] << 1) | (vals[3] >> 4)) & 0xFF))
        if pad == 4:
            i += 8
            continue
        result.append(uint8((((vals[3] & 0x0F) << 4) | (vals[4] >> 1)) & 0xFF))
        if pad == 3:
            i += 8
            continue
        result.append(uint8((((vals[4] & 0x01) << 7) | (vals[5] << 2) | (vals[6] >> 3)) & 0xFF))
        if pad == 1:
            i += 8
            continue
        result.append(uint8((((vals[6] & 0x07) << 5) | vals[7]) & 0xFF))
        i += 8
    return bytes(result)

_NEWLINE: int32 = 10  # '\n'
_MIME_LINE: int32 = 76  # MIME line length for encodebytes

def encodebytes(data: bytes) -> bytes:
    # MIME-style: standard base64 with a newline every 76 output chars and
    # a trailing newline. For empty input, returns b'' (no newline).
    encoded: bytes = _b64_encode(data, _B64_STD)
    n: int32 = int32(len(encoded))
    result: bytearray = bytearray()
    i: int32 = 0
    while i < n:
        j: int32 = 0
        while j < _MIME_LINE and i < n:
            result.append(encoded[i])
            i += 1
            j += 1
        result.append(uint8(_NEWLINE))
    return bytes(result)

def decodebytes(data: bytes) -> bytes:
    # MIME-style: tolerate newlines and whitespace in input.
    return b64decode(data)

