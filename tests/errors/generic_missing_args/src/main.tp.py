from tpy import Int32

class Box[T]:
    value: T

    def __init__(self, value: T) -> None:
        self.value = value


def main() -> None:
    box = Box(42)  # tpyc: error(/requires type arguments/)


main()
