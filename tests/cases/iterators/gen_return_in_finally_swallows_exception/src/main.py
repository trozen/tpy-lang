# `return` inside a suspending `finally` swallows a raise from the try
# body. Expected: "before", "in finally", StopIteration -- no exception
# propagates.
from typing import Iterator


def gen() -> Iterator[str]:
    try:
        yield "before"
        raise ValueError("oops")
    finally:
        yield "in finally"
        return


def main() -> None:
    for v in gen():
        print(v)


main()
