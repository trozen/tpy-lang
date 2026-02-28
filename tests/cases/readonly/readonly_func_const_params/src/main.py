# @readonly on functions/methods emits const T& for non-value params,
# value-type params remain by-value.
from tpy import Int32, readonly

class Box:
    value: Int32
    def __init__(self, value: Int32) -> None:
        self.value = value

    @readonly
    def get_value(self) -> Int32:
        return self.value

@readonly
def read_box(b: Box, offset: Int32) -> Int32:
    return b.value + offset

@readonly
def sum_list(items: list[Int32]) -> Int32:
    total = Int32(0)
    for x in items:
        total = total + x
    return total

def main() -> None:
    b = Box(Int32(10))
    print(read_box(b, Int32(5)))
    nums: list[Int32] = [1, 2, 3]
    print(sum_list(nums))
    print(b.get_value())

main()
