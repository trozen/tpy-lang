# A failing assert_send prints the why-not chain down to the offending field.
from tpy import int32, Ptr, assert_send


class Order:
    handler: Ptr[int32]

    def __init__(self, h: Ptr[int32]) -> None:
        self.handler = h


def main() -> None:
    assert_send[list[Order]]()  # tpyc: error(/assert_send assertion failed: list\[Order\] is not Send/)


main()
