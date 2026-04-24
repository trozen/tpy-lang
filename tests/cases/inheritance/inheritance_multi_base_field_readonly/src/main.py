# @readonly context: BaseN.field reads are legal and, for non-value fields,
# propagate readonly so downstream code can't mutate through them.
from tpy import Int32, readonly


class A:
    buf: list[Int32]


class B:
    buf: list[str]


class Combined(A, B):
    def __init__(self) -> None:
        A.buf = [Int32(1), Int32(2)]
        B.buf = ["x", "y"]

    @readonly
    def total_int_len(self) -> Int32:
        nums = A.buf  # tpyc: type(/readonly\[list\[Int32\]\]/)
        return Int32(len(nums))

    @readonly
    def first_str(self) -> str:
        labels = B.buf  # tpyc: type(/readonly\[list\[str\]\]/)
        return labels[0]


def main() -> None:
    c = Combined()
    print(c.total_int_len())
    print(c.first_str())


main()
