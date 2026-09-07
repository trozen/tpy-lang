# A SECOND `assert isinstance` on the same subject after a suspension: the
# later assert lands in a block whose boundary restore already dropped the
# persistent narrowing fact, so it no longer classifies as a bump.
from typing import Iterator


def reassert(a: int | str) -> Iterator[str]:
    assert isinstance(a, int)
    yield str(a + 1)
    # The re-assert after the yield is the subject.
    assert isinstance(a, int)  # tpyc: error(/stmt\.assert/)
    yield str(a + 2)


def main() -> None:
    for s in reassert(1):
        print(s)


main()
