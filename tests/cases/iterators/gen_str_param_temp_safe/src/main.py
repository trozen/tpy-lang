# A str generator parameter is captured owned in the frame, so passing a
# temporary argument is safe across iteration (no borrow, no warning).
from typing import Iterator


def greetings(name: str) -> Iterator[str]:
    yield "hello " + name
    yield "goodbye " + name


def make_name() -> str:
    return "wor" + "ld"


def main() -> None:
    # make_name() is a temporary, destroyed at end-of-statement; the generator
    # owns its copy of `name`, so iteration still reads "world".
    for msg in greetings(make_name()):  # tpyc: ok
        print(msg)


main()
