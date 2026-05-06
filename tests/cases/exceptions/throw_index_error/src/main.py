# Explicit `raise IndexError(...)` works the same as runtime-thrown
# IndexError; user code can also subclass it (basic catch shape only).


def at(xs: list[int], i: int) -> int:
    if i < 0 or i >= len(xs):
        raise IndexError("custom: index out of bounds")
    return xs[i]


def main() -> None:
    print(at([10, 20, 30], 1))
    try:
        print(at([10, 20, 30], 99))
    except IndexError as e:
        print("caught:", str(e))


main()
