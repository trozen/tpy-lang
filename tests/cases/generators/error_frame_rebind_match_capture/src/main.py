# A match capture into a rebound generator-frame reference local is refused
# (BUGS.md#frame-match-capture-rebind-rejects); the fresh-subject capture is
# itself the rebind, giving `p` a new object while `keep` holds the old one.
from typing import Iterator

from tpy import int32


class P:
    n: int32

    def __init__(self, n: int32) -> None:
        self.n = n


def gen(v: P) -> Iterator[int32]:  # tpyc: error(/this construct is not yet supported by C\+\+ code generation \(stmt\.match\)/)
    p = P(1)
    keep = p
    yield p.n
    match P(v.n + 1):
        # The subject: the capture is `p`'s only rebind.
        case P() as p:
            p.n += 1
            yield p.n
    yield keep.n


def main() -> None:
    for x in gen(P(7)):
        print(x)


main()
