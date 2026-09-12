# Unpacking a tuple whose first element is `StrView | None`: an Optional-view
# unpack target is not lowered yet, so the case pins the reject.
from tpy import int32, StrView


def sink(v: str | None) -> int32:
    return 1 if v is not None else 0


def use(t: tuple[StrView | None, int32]) -> int32:
    # The `StrView | None` first element is the Optional-view unpack target.
    s, k = t  # tpyc: error(/stmt.tuple_unpack/)
    if s is not None:
        return k + len(s)
    return k


def main() -> None:
    print(use((StrView("ab"), 1)))


main()
