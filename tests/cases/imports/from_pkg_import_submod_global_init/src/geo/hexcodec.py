from tpy import int32, uint8

# A module-global bytes literal constant: codegen declares it empty at file
# scope and assigns it in this module's __tpy_init(). If that init is not
# chained, the constant stays empty and the indexing below panics.
_HEX: bytes = b"0123456789ABCDEF"

# A module-global built by a top-level statement (not a literal): only ever
# populated by __tpy_init running, so it double-guards that init actually ran.
_OFFSETS: list[int32] = [1, 2, 3]


def hex_byte(c: int32) -> bytes:
    out = bytearray()
    out.append(uint8(37))
    out.append(_HEX[c >> 4])
    out.append(_HEX[c & 0xF])
    return out.decode().encode()


def offset_sum() -> int32:
    total: int32 = 0
    for o in _OFFSETS:
        total += o
    return total
