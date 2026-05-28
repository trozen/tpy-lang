# Error: nesting two `yield`-in-`finally` regions is a planned follow-up.
# The inner suspending finally would need to forward a pending exception /
# return to the outer slot, which the CFG can't model yet, so sema rejects
# the inner try early. Mirrors the async `error_await_in_control_flow`
# case.
from typing import Iterator


def gen() -> Iterator[int]:
    try:
        try:  # tpyc: error(/nesting two .yield.-in-.finally. regions/)
            yield 1
        finally:
            yield 2
    finally:
        yield 3


def main() -> None:
    for v in gen():
        print(v)


main()
