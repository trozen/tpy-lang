from tpy import Int32


def _seed_b() -> Int32:
    return Int32(20) + Int32(2)


VAL_B: Int32 = _seed_b()  # tpyc: warning(/ALL_CAPS variable .* without Final/)
