# A str parameter of a simple single-yield generator (the make_generator +
# lambda path, vs gen_str_param's multi-yield resumable path) is captured owned
# via the lambda init-capture, so a temporary argument is safe across iteration.
from typing import Iterator


def echo_n(s: str, n: int) -> Iterator[str]:
    i = 0
    while i < n:
        yield s
        i += 1


def make() -> str:
    return "x" + "y"


def main() -> None:
    for v in echo_n(make(), 3):  # tpyc: ok
        print(v)


main()
