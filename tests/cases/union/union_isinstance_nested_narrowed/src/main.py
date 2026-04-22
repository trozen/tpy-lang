# isinstance() on a variable already narrowed to a concrete type folds to
# a constant at compile time (no redundant std::holds_alternative).
class A:
    x: int
    def __init__(self, x: int) -> None:
        self.x = x

class B:
    y: int
    def __init__(self, y: int) -> None:
        self.y = y

class C:
    z: int
    def __init__(self, z: int) -> None:
        self.z = z


def redundant_same(v: A | B) -> int:
    if isinstance(v, A):
        if isinstance(v, A):  # always true
            return v.x
    return -1


def dead_other(v: A | B) -> int:
    if isinstance(v, A):
        if isinstance(v, B):  # always false
            return -1  # dead
        return v.x
    return -2


def assign_then_check() -> int:
    v: A | B = A(7)
    if isinstance(v, A):  # always true after assignment narrowing
        return v.x
    return -1


def elif_exhaustive(v: A | B | C) -> str:
    if isinstance(v, A):
        return "a"
    elif isinstance(v, B):
        return "b"
    elif isinstance(v, C):  # exhaustive tail, static-true
        return "c"
    return "unreachable"


def main() -> None:
    print(redundant_same(A(1)))
    print(redundant_same(B(2)))

    print(dead_other(A(5)))
    print(dead_other(B(9)))

    print(assign_then_check())

    print(elif_exhaustive(A(1)))
    print(elif_exhaustive(B(2)))
    print(elif_exhaustive(C(3)))


main()
