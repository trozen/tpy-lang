from tpy import Int32, Array


class Buffer[T, N: int]:
    data: Array[T, N]

    def __init__(self) -> None:
        pass

    def set(self, idx: Int32, val: T) -> None:
        self.data[idx] = val

    def get(self, idx: Int32) -> T:
        return self.data[idx]


def main() -> None:
    b: Buffer[Int32, 3] = Buffer[Int32, 3]()

    # Set values
    b.set(Int32(0), Int32(10))
    b.set(Int32(1), Int32(20))
    b.set(Int32(2), Int32(30))

    # Get values
    print(b.get(Int32(0)))
    print(b.get(Int32(1)))
    print(b.get(Int32(2)))


main()
