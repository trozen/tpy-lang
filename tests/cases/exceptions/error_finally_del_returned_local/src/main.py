# del of a local borrowed by a finally-deferred return is rejected: the
# pending return materializes from that storage after the finally runs.
from tpy import int32, Own


class Box:
    n: int32

    def __init__(self) -> None:
        self.n = 10


def f() -> Own[Box]:
    b = Box()
    try:
        return b
    finally:
        b.n += 1
        del b  # tpyc: error(/cannot delete 'b' in this finally block/)


def main() -> None:
    print(f().n)


main()
