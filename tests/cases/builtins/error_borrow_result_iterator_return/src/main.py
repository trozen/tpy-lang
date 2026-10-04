# max() over an iterator hands back a COPY of the element (an iterator's step
# is valid only until the next one), so returning it through a borrowing
# `-> P` would return a temporary: the existing dangling-return error
# (docs/LANGUAGE_FEATURES.md, the min / max bullet).
from typing import Iterator


class P:
    v: int

    def __init__(self, v: int) -> None:
        self.v = v


def key_of(p: P) -> int:
    return p.v


def gen() -> Iterator[P]:
    for v in [1, 3, 2]:
        p = P(v)
        yield p


def top() -> P:
    g = gen()
    return max(g, key=key_of)  # tpyc: error(/Cannot return local or temporary as reference/)


def main() -> None:
    print(top().v)


main()
