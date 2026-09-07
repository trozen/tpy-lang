# A loop variable over an Own-yielding generator that stays LOOP-SCOPED: the
# binding dies with the iteration, so each element is moved into the container.
from typing import Iterator
from tpy import Int32, Own


def gen() -> Iterator[Own[Int32]]:
    yield 1
    yield 2


def collect_scoped() -> None:
    out: list[Int32] = []
    for x in gen():  # tpyc: ok -- the loop-scoped binding, moved per element
        out.append(x)
    print(out)


def main() -> None:
    collect_scoped()


main()
