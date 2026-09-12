# Generic class with an Array[T, N] field. Buffer.get() returns T from self.data[idx]
# (a mutable element reference), so it must not be inferred const: val_or_ref_t<T>, no const.
from tpy import int32, Array


class Buffer[T, N: int]:
    data: Array[T, N]

    def __init__(self) -> None:
        pass

    def set(self, idx: int32, val: T) -> None:
        self.data[idx] = val

    def get(self, idx: int32) -> T:
        return self.data[idx]


def main() -> None:
    b: Buffer[int32, 3] = Buffer[int32, 3]()

    # Set values
    b.set(int32(0), int32(10))
    b.set(int32(1), int32(20))
    b.set(int32(2), int32(30))

    # Get values
    print(b.get(int32(0)))
    print(b.get(int32(1)))
    print(b.get(int32(2)))


main()
