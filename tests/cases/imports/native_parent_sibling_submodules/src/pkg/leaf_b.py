from tpy import int32


def _seed_b() -> int32:
    return int32(20) + int32(2)


VAL_B: int32 = _seed_b()  # tpyc: warning(/ALL_CAPS variable .* without Final/)
