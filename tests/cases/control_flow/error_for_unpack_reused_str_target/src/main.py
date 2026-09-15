# A str unpack target that REUSES an enclosing slot keeps rejecting. The
# scalar twin assigns the slot (for_unpack_reused_target), but a str slot may
# be the view form while the element is owned, and the assign would leave the
# view pointing into the loop's holder -- picking the slot's spelling is the
# declaration's job, not this arm's.
from tpy import int32


def f(pairs: list[tuple[str, int32]]) -> str:
    s = "start"
    for s, n in pairs:  # tpyc: error(/not yet supported.*tuple\.reused_target/)
        pass
    return s


def main() -> None:
    print(f([("a", 1), ("b", 2)]))


main()
