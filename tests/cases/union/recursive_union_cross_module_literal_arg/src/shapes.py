from tpy import Int32

type Shape = Int32 | str | list[Shape]


def int_leaves(s: Shape) -> Int32:
    match s:
        case list() as items:
            total = 0
            for it in items:
                total += int_leaves(it)
            return total
        case str():
            return 0
        case _:
            return 1
