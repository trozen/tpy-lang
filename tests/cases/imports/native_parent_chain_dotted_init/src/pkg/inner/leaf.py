from tpy import int32


def _seed() -> int32:
    # Non-trivial init so __tpy_init() runs (not a constexpr fold).
    return int32(40) + int32(2)


COUNTER: int32 = _seed()  # tpyc: warning(/ALL_CAPS variable .* without Final/)
