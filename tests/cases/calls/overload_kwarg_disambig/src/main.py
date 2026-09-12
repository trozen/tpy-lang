# Keyword arguments participate in overload resolution tier ranking.
#
# Before the fix, resolve_overload only saw positional args; _resolve_call_kwargs
# ran post-resolution against the already-picked winner. Two overloads that
# share a positional shape but differ only in a kwarg's type would either
# tie at the top of scoring (ambiguous) or pick the first-declared overload
# and then fail the kwarg type-check. With the fix, per-overload expansion
# inserts each kwarg's type at its matching param slot so the kwarg can
# break the tie.
from tpy import int32, dispatch


@dispatch
def pick(x: int32, *, tag: str = "") -> str:
    return tag + ":" + str(x)


@dispatch
def pick(x: int32, *, tag: int32 = 0) -> int32:
    return x + tag


def main() -> None:
    # Positional arg alone is ambiguous (int32 matches both overloads at the
    # same tier); the kwarg's type picks the winner. Before the fix,
    # resolve_overload didn't see kwargs -- both calls raised "Ambiguous
    # overload" since the positional signatures are identical.
    a: str = pick(int32(10), tag="label")
    print(a)                                # label:10
    b: int32 = pick(int32(10), tag=int32(5))
    print(b)                                # 15


main()
