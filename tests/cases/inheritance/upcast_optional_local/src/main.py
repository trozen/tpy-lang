# A plain upcast of a record lvalue into a nullable base-typed local. The slot
# is a pointer, so the bind is the address of the same object -- no copy, no
# representation change: mutating through the base handle is visible through
# the derived one.
class Pet:
    name: str
    tag: int

    def __init__(self, name: str) -> None:
        self.name = name
        self.tag = 0

    def rename(self, name: str) -> None:
        self.name = name


class Dog(Pet):
    def __init__(self, name: str) -> None:
        super().__init__(name)


def upcast(d: Dog) -> None:
    p: Pet | None = d  # the base handle aliases `d`, it does not copy it
    if p is not None:
        p.rename("via-base")  # ... so this write is visible through `d`
    print(d.name)


def main() -> None:
    d = Dog("rex")
    upcast(d)
    print(d.name)


main()
