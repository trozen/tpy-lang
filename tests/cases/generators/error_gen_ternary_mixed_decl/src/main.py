# A generator local bound from a ternary of an existing list and a fresh one
# would alias the existing arm; a frame cannot hold that alias yet, so the
# binding is refused rather than copied, as the both-name ternary is
# (BUGS.md#reference-ternary-position-gaps).
from typing import Iterator
from tpy import int32


def gen(b: list[int32], c: bool) -> Iterator[int32]:
    x = b if c else [9]  # tpyc: error(/res\.alias_bind/)
    x.append(1)
    yield len(b)


def main() -> None:
    for v in gen([1], True):
        print(v)


main()
