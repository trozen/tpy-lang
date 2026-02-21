from tpy import Int32

class Box[T]:
    value: T

    def __init__(self, value: T) -> None:
        self.value = value

    def get(self) -> T:
        return self.value


def main() -> None:
    # Box containing a list (object type)
    items: list[Int32] = [1, 2, 3]
    box: Box[list[Int32]] = Box[list[Int32]](items)

    # This should work: get() returns T& for object types, allowing mutation
    box.get().append(4)
    box.get().append(5)

    # Verify the mutations persisted
    for x in box.get():
        print(x)


main()
