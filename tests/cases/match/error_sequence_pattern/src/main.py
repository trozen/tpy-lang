# Sequence patterns (`case [x, y]:`) are valid Python that TPy does not support yet
# (docs/LANGUAGE_FEATURES.md, match/case: "Sequence patterns").


def first_two(xs: list[int]) -> int:
    match xs:
        case [a, b]:  # tpyc: error(/Sequence, mapping and star patterns are not supported yet/)
            return a + b
        case _:
            return 0


def main() -> None:
    print(first_two([1, 2]))


main()
