# del of a nonlocal inside a closure is rejected when an enclosing finally-
# deferred return borrows the name (the closure could free the storage the
# pending return materializes from).
from tpy import int32, Own


class Box:
    n: int32

    def __init__(self) -> None:
        self.n = 10


def f() -> Own[Box]:
    b = Box()

    def cleanup() -> None:
        nonlocal b
        b.n += 1
        del b  # tpyc: error(/cannot delete nonlocal 'b' here/)

    try:
        return b
    finally:
        cleanup()


def main() -> None:
    print(f().n)


main()
