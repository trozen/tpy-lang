# A @dispatch variant gets every check a plain def does: a generator variant
# whose return is not `Iterator[T]` is refused at its def (the documented
# rule, docs/LANGUAGE_FEATURES.md "Generators": a generator returns
# `Iterator[T]`).
from typing import Iterator
from tpy import dispatch, int32


@dispatch
def gen(x: int32) -> Iterator[int32]:
    yield x


@dispatch
def gen(x: str) -> int32:  # tpyc: error(/Generator function must have return type 'Iterator\[T\]', got 'int32'/)
    yield len(x)


def main() -> None:
    for v in gen(1):
        print(v)


main()
