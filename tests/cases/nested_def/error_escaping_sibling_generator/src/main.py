# A nested def in a generator lives on the frame, so a lambda that carries it
# out of the generator is refused as handing the def out directly is.
from typing import Callable, Iterator


def gen() -> Iterator[Callable[[], float]]:
    x = 7.0

    def h() -> float:
        return x
    yield lambda: h()  # tpyc: error(/nested function 'h' defined in a generator cannot escape/)


def main() -> None:
    for f in gen():
        print(f())


main()
