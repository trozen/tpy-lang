# str tuple targets assigned in match arms, each from a DISTINCT arm-local
# source (t1 / t2 -- assigned in only one arm, so neither is hoisted). The
# targets a/b ARE hoisted (assigned on all arms, read after the match), so they
# outlive their arm-local source and must own (std::string) -- a view would
# dangle. Covers the owned-promotion at the match predecl site (not just if/else);
# a shared `t` name would hoist the source and make a view safe.
from tpy import int32


def make(tag: str) -> tuple[str, str]:
    return (tag + "-alpha-long-enough-to-heap", tag + "-beta-long-enough-to-heap")


def main() -> None:
    n = len("ab")
    match n:
        case 2:
            t1 = make("X")
            a, b = t1
        case _:
            t2 = make("Y")
            a, b = t2
    print(a)
    print(b)


main()
