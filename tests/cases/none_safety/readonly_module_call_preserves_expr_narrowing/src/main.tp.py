from tpy import Int32, readonly
import helpers as h

@readonly
def use_after_check(items: list[Int32 | None], i: Int32) -> Int32:
    if items[i] is not None:
        h.noop()
        return items[i] + 1  # tpyc: ok
    return 0


def main() -> None:
    values: list[Int32 | None] = []
    values.append(Int32(10))
    print(use_after_check(values, 0))


main()
