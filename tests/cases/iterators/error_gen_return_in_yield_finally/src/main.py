# Error: a `return` inside a `finally` body that itself yields is a planned
# follow-up -- the CFG-based finally can't yet park the pending return
# across the suspending finally, so sema rejects it early. A `return` in
# the try/except/with body (not the finally) stays supported; only a
# return from within the suspending finally is blocked.
from typing import Iterator


def gen() -> Iterator[int]:
    try:  # tpyc: error(/return. inside a finally body that itself contains/)
        yield 1
    finally:
        yield 2
        return


def main() -> None:
    for v in gen():
        print(v)


main()
