# A @nocopy Own element of a tuple parameter read after its field store cannot
# move into the field, and a copy is refused (docs/LANGUAGE_FEATURES.md "Borrow
# Form vs Storage Form": a type that cannot be copied is an error, not a copy).
from tpy import Own, int32, nocopy


@nocopy
class G:
    def __init__(self, x: int32) -> None:
        self.x = x


class A:
    def __init__(self, x: int32) -> None:
        self.x = x


class GH:
    g: tuple[G, A]

    def __init__(self) -> None:
        self.g = (G(0), A(0))

    # method: the Own element is read after the store, so it cannot move
    def put(self, p: tuple[Own[G], A]) -> int32:
        self.g = p  # tpyc: error(/cannot copy non-copyable type .G. into field \(tuple element 0\)/)
        return p[0].x


def main() -> None:
    print(GH().put((G(1), A(2))))


main()
