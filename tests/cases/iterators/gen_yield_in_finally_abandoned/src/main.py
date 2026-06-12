# A finally containing yield cannot run inside the frame destructor:
# abandonment skips it (warned at compile time). CPython instead runs it
# up to the yield and raises RuntimeError("generator ignored GeneratorExit")
# -- acknowledged divergence, hence no CPython phase.
from typing import Iterator


def gen() -> Iterator[int]:
    try:
        yield 1
        yield 2
    finally:
        print("pre")
        yield 99  # tpyc: warning(/'yield' inside 'finally'/)
        print("post")


def main() -> None:
    for x in gen():
        print(x)
        break
    print("after")


main()
