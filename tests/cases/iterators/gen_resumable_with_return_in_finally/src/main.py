# Regression: outer with.__exit__ must be called when inner finally has return.
# With the C1 bug, the inline stop-check after the inner finally helper would
# return StopIteration before calling outer with.__exit__.
from typing import Iterator


class CM:
    def __init__(self, name: str) -> None:
        self.name = name

    def __enter__(self) -> "CM":
        print(f"enter {self.name}")
        return self

    def __exit__(self, exc_type, exc_val, exc_tb) -> bool:
        print(f"exit {self.name}")
        return False


def gen_with_outer_return_in_finally() -> Iterator[int]:
    # Outer with must have __exit__ called even though inner finally returns.
    with CM("outer"):
        try:
            yield 1
        finally:
            return


def gen_nested_with_return_in_finally() -> Iterator[int]:
    # Both outer and inner with.__exit__ must be called.
    with CM("outer"):
        with CM("inner"):
            try:
                yield 1
            finally:
                return


def main() -> None:
    print("--- gen_with_outer_return_in_finally ---")
    for x in gen_with_outer_return_in_finally():
        print(x)
    print("--- gen_nested_with_return_in_finally ---")
    for x in gen_nested_with_return_in_finally():
        print(x)


main()
