from tpy import Int32


def _seed() -> Int32:
    # Non-trivial init: forces __tpy_init() rather than constexpr fold.
    return Int32(20) + Int32(22)


COUNTER: Int32 = _seed()  # tpyc: warning(/ALL_CAPS variable .* without Final/)
