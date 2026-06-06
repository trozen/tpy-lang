# Function call returning tuple[T | None, ...] used as the source for
# storage-form destinations (list.append, dict subscript-assign). The call
# returns pointer form, so the wrap path must fire -- is_storage_form_source
# returns False for calls.
from tpy import Int32


class P:
    x: Int32
    def __init__(self, x: Int32) -> None:
        self.x = x


def make_pair(left: P, right: P) -> tuple[P | None, P | None]:
    return (left, right)


def main() -> None:
    a = P(1)
    b = P(2)

    pairs: list[tuple[P | None, P | None]] = []
    pairs.append(make_pair(a, b))

    d: dict[Int32, tuple[P | None, P | None]] = {}
    d[Int32(0)] = make_pair(a, b)  # tpyc: warning(/copies/) warning(/copies/)

    print(len(pairs))
    print(len(d))


main()
