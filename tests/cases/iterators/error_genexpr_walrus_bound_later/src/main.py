# The enclosing function must bind a generator-expression walrus target before
# the statement; one it binds only after the expression may not be assigned.
from typing import Iterable, Iterator


def relay(it: Iterable[int]) -> Iterator[int]:
    for v in it:
        yield v


def run() -> None:
    # The subject: `x` is bound by the later `x = 10`, not before the frame.
    g = relay(x := int(i) for i in range(3))  # tpyc: error(/variable 'x' may not be assigned at this point/)
    x = 10
    print(list(g), x)


run()
