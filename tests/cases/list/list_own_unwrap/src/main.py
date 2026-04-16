# OwnType must be stripped from element types in list literals so that
# non-last-use variables don't cause spurious "mixed types" errors.
# Also tests ptr-variant -> value-variant conversion for container storage.


class Cat:
    name: str

    def __init__(self, name: str) -> None:
        self.name = name


class Dog:
    name: str

    def __init__(self, name: str) -> None:
        self.name = name


def show(a: Cat | Dog) -> None:
    pass


def main() -> None:
    a: Cat | Dog = Cat("x")
    b: Cat | Dog = Dog("y")
    items: list[Cat | Dog] = [a, b]
    show(a)
    show(b)
    print(len(items))

    # Narrowed variable in list literal: 'a' is Cat& after isinstance,
    # must not be wrapped with to_value_variant.
    if isinstance(a, Cat):
        more: list[Cat | Dog] = [a, b]
        print(len(more))

main()
