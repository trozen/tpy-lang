from tpy import int32

class Pair[A, B]:
    first: A
    second: B

    def __init__(self, first: A, second: B) -> None:
        self.first = first
        self.second = second

    def get_first(self) -> A:
        return self.first

    def get_second(self) -> B:
        return self.second


def main() -> None:
    # Test with int32 and str
    p1: Pair[int32, str] = Pair[int32, str](42, "hello")
    print(p1.get_first())
    print(p1.get_second())

    # Test with str and int32 (reversed)
    p2: Pair[str, int32] = Pair[str, int32]("world", 100)
    print(p2.get_first())
    print(p2.get_second())


main()
