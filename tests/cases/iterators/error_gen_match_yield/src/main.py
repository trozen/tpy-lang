# Error: a `yield` inside a `match` is not yet supported on the resumable
# frame -- `match` is the one compound the CFG builder does not decompose,
# so a suspension inside it cannot be lowered. Sema rejects it early with a
# clean located diagnostic; the workaround is to move the `match` outside
# the generator or factor each arm into its own helper.
from typing import Iterator


def gen(n: int) -> Iterator[int]:
    match n:  # tpyc: error(/inside a .match. statement is not yet supported/)
        case 0:
            yield 10
            yield 20
        case _:
            yield 30


def main() -> None:
    for v in gen(0):
        print(v)


main()
