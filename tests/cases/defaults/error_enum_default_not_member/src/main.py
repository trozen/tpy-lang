# A dotted default that does not resolve to an enum member is rejected. The
# parser accepts the Name.attr shape (it has no type info yet); registration is
# the gate. CPython accepts a class attribute as a default, so this is TPy-only.
class Config:
    LIMIT: int = 10


def f(x: int = Config.LIMIT) -> int:  # tpyc: error(/not a resolvable enum member/)
    return x


def main() -> None:
    print(f())


main()
