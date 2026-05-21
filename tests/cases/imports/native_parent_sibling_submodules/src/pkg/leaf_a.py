from tpy import Int32


def _seed_a() -> Int32:
    return Int32(10) + Int32(1)


VAL_A: Int32 = _seed_a()  # tpyc: warning(/ALL_CAPS variable .* without Final/)
