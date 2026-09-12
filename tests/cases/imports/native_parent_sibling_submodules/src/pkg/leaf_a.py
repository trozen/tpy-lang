from tpy import int32


def _seed_a() -> int32:
    return int32(10) + int32(1)


VAL_A: int32 = _seed_a()  # tpyc: warning(/ALL_CAPS variable .* without Final/)
