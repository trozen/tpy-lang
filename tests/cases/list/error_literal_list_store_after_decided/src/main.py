# A use that decides an unannotated list literal's element is named by the
# refusal of a later wider store (docs/LANGUAGE_FEATURES.md "List Literal Inference").
from tpy import int64, Equatable


def has_item[T: Equatable](xs: list[T], v: T) -> bool:
    for x in xs:
        if x == v:
            return True
    return False


def w64() -> int64:
    return 5000000000


def main() -> None:
    ys = [1, 2]
    print(has_item(ys, 2))
    # The generic call above was instantiated over int32 elements.
    ys.append(w64())  # tpyc: error(/'ys' holds int32 elements since line \d+ \(passed as list\[int32\]\), and this value is int64; annotate its first binding: ys: list\[int64\] = \[\.\.\.\]/)
    print(ys)


main()
