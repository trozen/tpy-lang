from tpy import Int32

class Box[T]:
    value: T

    def __init__(self, value: T) -> None:
        self.value = value


def main() -> None:
    box: Box[Int32, str] = Box[Int32, str](42)  # tpyc: error(/expects 1 type arguments/)


main()
