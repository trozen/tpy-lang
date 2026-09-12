from tpy import int32


class Matrix[T, N: int]:
    value: T

    def __init__(self, v: T) -> None:
        self.value = v


def process_matrix(m: Matrix[int32, 4]) -> None:
    print(m.value)


def main() -> None:
    m8: Matrix[int32, 8] = Matrix[int32, 8](int32(42))
    process_matrix(m8)  # tpyc: error(/mismatch.*Matrix/)


main()
