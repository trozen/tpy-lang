# yield inside a finally block (CFG-based finally on the resumable frame).
# A bare `return` sets the pending-return flag; the finally body runs as
# part of the state machine, yielding before StopIteration is raised.
from typing import Iterator

def gen_return_then_finally_yield() -> Iterator[int]:
    try:
        return
    finally:
        yield 99

def gen_exception_then_finally_yield(x: int) -> Iterator[int]:
    try:
        if x < 0:
            raise ValueError("negative")
        yield x
    except ValueError:
        yield -1
    finally:
        yield 0

def main():
    print(list(gen_return_then_finally_yield()))
    print(list(gen_exception_then_finally_yield(5)))
    print(list(gen_exception_then_finally_yield(-1)))

main()
