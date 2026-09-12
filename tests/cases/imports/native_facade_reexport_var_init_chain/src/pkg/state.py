from tpy import int32


def _seed() -> int32:
    # Non-trivial init: forces __tpy_init() rather than constexpr fold.
    return int32(20) + int32(22)


COUNTER: int32 = _seed()  # tpyc: warning(/ALL_CAPS variable .* without Final/)
