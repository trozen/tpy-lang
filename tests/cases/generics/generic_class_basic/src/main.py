from tpy import int32

class Box[T]:
    value: T

    def __init__(self, value: T) -> None:
        self.value = value

    def get(self) -> T:
        return self.value

    def set(self, value: T) -> None:
        self.value = value


def main() -> None:
    # Test with int32
    box_int: Box[int32] = Box[int32](42)
    print(box_int.get())
    box_int.set(100)
    print(box_int.get())

    # Test with str
    box_str: Box[str] = Box[str]("hello")
    print(box_str.get())
    box_str.set("world")
    print(box_str.get())

    # Test type deduction (no explicit annotation)
    box_deduced = Box[int32](999)
    print(box_deduced.get())


main()
