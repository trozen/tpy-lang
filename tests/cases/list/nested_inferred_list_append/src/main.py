# Appending an owned inner list into an UNANNOTATED outer list: both sides
# carry the same unresolved literal type, so the Own-slot arg row has to
# resolve the SLOT as well as the argument before comparing them.
from tpy import Int32, Own


def rows(src: list[list[float]]) -> Own[list[list[float]]]:
    outer = []
    for row in src:
        inner = []
        for x in row:
            inner.append(x * 2.0)
        outer.append(inner)  # inner moves into the unannotated outer list
    return outer


def main() -> None:
    base: list[list[float]] = []
    base.append([1.0, 2.0])
    out = rows(base)
    out[0].append(9.0)
    print(len(out), len(out[0]), out[0][2])
    # The same shape at a LOCAL outer list, both sides still unannotated.
    local = []
    part = []
    part.append(1)
    local.append(part)
    local[0].append(2)
    print(len(local), len(local[0]), local[0][1])


main()
