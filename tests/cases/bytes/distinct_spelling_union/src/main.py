# Unions whose members USED to share one C++ spelling. Each str/bytes type now
# renders its own C++ type, so `bytes | bytearray`, `str | String` and
# `list[UInt8] | bytes` all get distinguishable variant alternatives instead of
# a repeated one, and the isinstance arm and the `Own[union]` return slot the
# member class used to refuse both lower. Passing a concrete member INTO such a
# union parameter is a separate pre-existing lowering gap for the bytes-family
# and str-family pairs (identical on master), so those two are pinned by their
# emitted signature and body; only the `list[UInt8] | bytes` pair is called.
from tpy import Int32, Own, String, UInt8


def bytes_or_bytearray(u: bytes | bytearray) -> Int32:
    # bytes and bytearray: the pair that could not be told apart at all. Not
    # CALLED: BUGS.md#bytes-member-arg-not-lifted-into-union
    if isinstance(u, bytearray):  # tpyc: ok
        return 10 + len(u)
    return 20 + len(u)


def str_or_string_slot(u: str | String) -> Int32:
    # the str-family pair, unspellable for the same reason. Not CALLED:
    # BUGS.md#bytes-member-arg-not-lifted-into-union
    return 7  # tpyc: ok


def list_or_bytes(u: list[UInt8] | bytes) -> Int32:
    # list[UInt8] keeps the plain buffer spelling, so it is distinct too
    if isinstance(u, bytes):  # tpyc: ok
        return 50 + len(u)
    return 60 + len(u)


def own_union_return(n: Int32) -> Own[bytes | bytearray]:
    # the Own[union] STORAGE slot, the second consumer of the member class.
    # Not CALLED: reading a member back out is the same gap,
    # BUGS.md#bytes-member-arg-not-lifted-into-union
    b = bytearray(n)
    return b  # tpyc: ok


def main() -> None:
    xs: list[UInt8] = [UInt8(1), UInt8(2)]
    # the subject that runs: each member reaches its own arm
    print("union", list_or_bytes(xs), list_or_bytes(b"ijk"))


main()
