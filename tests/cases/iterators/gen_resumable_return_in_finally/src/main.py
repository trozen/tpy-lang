# Tests `return` inside a non-suspending finally body of a resumable-frame
# generator: sets __finally_stop so __next__() emits StopIteration instead of
# re-throwing (Python: `return` in `finally` suppresses any pending exception).
from typing import Iterator

def gen_return_normal() -> Iterator[int]:
    try:
        yield 1
    finally:
        return

def gen_return_suppresses_exc() -> Iterator[int]:
    try:
        yield 1
        raise ValueError("suppressed")
    finally:
        return

def gen_return_in_loop() -> Iterator[int]:
    for i in range(5):
        try:
            yield i
        finally:
            if i == 2:
                return

def main():
    print(list(gen_return_normal()))
    print(list(gen_return_suppresses_exc()))
    print(list(gen_return_in_loop()))

main()
