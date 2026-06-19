# A PEP 695 `type`-statement alias name is not isinstance-able: CPython
# rejects a TypeAliasType at runtime (every version, by core-dev decision).
# The inline union / tuple form are the runtime-valid spellings.
class Circle:
    def __init__(self) -> None:
        pass


class Rect:
    def __init__(self) -> None:
        pass


type Shape = Circle | Rect


def f(s: Circle | Rect | int) -> None:
    if isinstance(s, Shape):  # tpyc: error(/does not accept the union alias 'Shape'/)
        print("shape")


def main() -> None:
    pass


main()
