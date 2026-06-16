# A deferred (post-sema) function macro reads an argument's inferred Int32 type
# (unavailable at pass 5.5) and rewrites sentinel(a, b) -> a + b. combine(3, 4) -> 7.
from sentinelmod import resolve_sentinel
from tpy import Int32


def sentinel(a: Int32, b: Int32) -> Int32:
    return 0


@resolve_sentinel
def combine(a: Int32, b: Int32) -> Int32:
    return sentinel(a, b)


def main() -> None:
    print(combine(3, 4))


main()
