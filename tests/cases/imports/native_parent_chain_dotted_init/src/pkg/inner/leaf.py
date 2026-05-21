from tpy import Int32


def _seed() -> Int32:
    # Non-trivial init so __tpy_init() runs (not a constexpr fold).
    return Int32(40) + Int32(2)


COUNTER: Int32 = _seed()  # tpyc: warning(/ALL_CAPS variable .* without Final/)
