# An instance no use completes (LANGUAGE_FEATURES "Deferred generic instance
# inference"): the hint names the class, which `cls` is no spelling of.
from tpy import copy, int32


class W[T]:
    items: list[T]

    def __init__(self) -> None:
        self.items = []

    def put(self, v: T) -> None:
        self.items.append(copy(v))

    @classmethod
    def make(cls) -> None:
        b = cls()  # tpyc: error(/add explicit type args \(e\.g\., W\[T\]\(\)\)/)
        print("made")


def main() -> None:
    W[int32].make()


main()
