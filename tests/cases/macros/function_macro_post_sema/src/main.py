# A deferred (post-sema) function macro reads an argument's inferred int32 type
# (unavailable at pass 5.5) and rewrites sentinel(a, b) -> a + b. combine(3, 4) -> 7.
from sentinelmod import resolve_sentinel
from tpy import int32


def sentinel(a: int32, b: int32) -> int32:
    return 0


@resolve_sentinel
def combine(a: int32, b: int32) -> int32:
    return sentinel(a, b)


def main() -> None:
    print(combine(3, 4))


main()
