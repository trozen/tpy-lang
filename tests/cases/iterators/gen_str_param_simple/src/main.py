# A str parameter of a single-yield generator is copied into owned frame
# storage on the way in (as in gen_str_param's multi-yield twin), so a
# temporary argument is safe across iteration.
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
