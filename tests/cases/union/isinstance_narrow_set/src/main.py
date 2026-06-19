# Bare-generic isinstance narrows a union to its set member (the third
# container sibling alongside dict / list). Mutating the narrowed set and
# observing the new length proves it aliases the union's storage, not a copy.
def f(x: int | set[int]) -> None:
    if isinstance(x, set):
        x.add(9)
        print(len(x))
    else:
        print("int")


def main() -> None:
    s: set[int] = {1, 2, 3}
    v: int | set[int] = s
    f(v)
    w: int | set[int] = 7
    f(w)


main()
