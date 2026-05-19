# Async methods on generic classes are rejected (parallel to the
# existing generator-method-on-generic-class rejection). M4's
# out-of-line coro-struct `__poll__` body doesn't yet receive the
# class's template header, so any reference to the class's type
# params would fail C++ build.
from typing import Optional


class Box[T]:
    value: T

    def __init__(self, v: T) -> None:
        self.value = v

    async def take(self) -> T:  # tpyc: error(/Async methods on generic classes are not yet supported/)
        return self.value


def main() -> None:
    print("ok")


main()
