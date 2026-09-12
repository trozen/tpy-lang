# @readonly on functions/methods emits const T& for non-value params,
# value-type params remain by-value.
from tpy import int32, readonly

class Box:
    value: int32
    def __init__(self, value: int32) -> None:
        self.value = value

    @readonly
    def get_value(self) -> int32:
        return self.value

@readonly
def read_box(b: Box, offset: int32) -> int32:
    return b.value + offset

@readonly
def sum_list(items: list[int32]) -> int32:
    total = int32(0)
    for x in items:
        total = total + x
    return total

def main() -> None:
    b = Box(int32(10))
    print(read_box(b, int32(5)))
    nums: list[int32] = [1, 2, 3]
    print(sum_list(nums))
    print(b.get_value())

main()
