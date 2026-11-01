# A Span local handed to a generator's protocol parameter is refused, as at
# its concrete Span twin (BUGS.md#frame-span-local-arg).
from typing import Iterator, Sized
from tpy import Span, int32


def gen_len(v: Sized) -> Iterator[int32]:
    yield len(v)


def main() -> None:
    xs = [1, 2, 3]
    sp: Span[int32] = xs
    # The subject: the frame would hold a view of storage it cannot name.
    g = gen_len(sp)  # tpyc: error(/not yet supported by C\+\+ code generation/)
    for n in g:
        print(n)


main()
