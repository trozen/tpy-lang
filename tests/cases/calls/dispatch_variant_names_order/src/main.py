# The call names the variant sema ranked first although a weaker one is
# declared first (winner-first twin: calls/dispatch_variant_names).
from typing import Protocol
from tpy import dispatch, int32


# --- a generic over a list shape returning T beside a protocol variant:
# the shape variant wins for a list ---
class HasLen(Protocol):
    def __len__(self) -> int: ...


@dispatch
def head(x: HasLen) -> int32:
    return -1


@dispatch
def head[T](xs: list[T]) -> T:
    return xs[0]


def head_section() -> None:
    xs: list[int32] = [7, 8]
    print("head:", head(xs))  # tpyc: ok
    xs[0] = 9
    print("head:", head(xs))


def main() -> None:
    head_section()


main()
