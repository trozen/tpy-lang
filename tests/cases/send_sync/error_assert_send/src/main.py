# A failing assert_send prints the why-not chain down to the offending field.
from tpy import Int32, Ptr, assert_send


class Order:
    handler: Ptr[Int32]

    def __init__(self, h: Ptr[Int32]) -> None:
        self.handler = h


def main() -> None:
    assert_send[list[Order]]()  # tpyc: error(/assert_send assertion failed: list\[Order\] is not Send/)


main()
