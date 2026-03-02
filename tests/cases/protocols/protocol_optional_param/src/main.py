# Optional[StaticProtocol] parameter: pointer repr codegen with
# if constexpr narrowing, explicit None, omitted optional args,
# required+optional mix, and generic Optional[Protocol].
from typing import Optional, Sized, Sequence

class Container:
    count: int

    def __init__(self, items: Optional[Sized] = None) -> None:
        if items is not None:
            self.count = len(items)
        else:
            self.count = 0

    def update(self, items: Sized, extra: Optional[Sized] = None) -> None:
        self.count = len(items)
        if extra is not None:
            self.count = self.count + len(extra)

def count_items(items: Sized, extra: Optional[Sized] = None) -> int:
    result: int = len(items)
    if extra is not None:
        result = result + len(extra)
    return result

def only_optional(items: Optional[Sized] = None) -> int:
    if items is not None:
        return len(items)
    return 0

# Required + optional protocol params in constructor
class MixedContainer:
    count: int

    def __init__(self, items: Sized, extra: Optional[Sized] = None) -> None:
        self.count = len(items)
        if extra is not None:
            self.count = self.count + len(extra)

# Generic Optional[Protocol] param: Optional[Sequence[int]]
def sum_optional(items: Sequence[int], extra: Optional[Sequence[int]] = None) -> int:
    result: int = 0
    i: int = 0
    while i < len(items):
        result = result + items[i]
        i = i + 1
    if extra is not None:
        j: int = 0
        while j < len(extra):
            result = result + extra[j]
            j = j + 1
    return result

def main() -> None:
    # Constructor with no args (delegating default ctor)
    c = Container()
    print(c.count)

    # Constructor with concrete arg
    nums: list[int] = [1, 2, 3]
    c2 = Container(nums)
    print(c2.count)

    # Constructor with explicit None
    c3 = Container(None)
    print(c3.count)

    # Method: required + optional protocol, both provided
    more: list[int] = [10, 20]
    c2.update(nums, more)
    print(c2.count)

    # Method: explicit None for optional
    c2.update(nums, None)
    print(c2.count)

    # Free function: both provided
    print(count_items(nums, more))

    # Free function: omit optional
    print(count_items(nums))

    # Free function: explicit None
    print(count_items(nums, None))

    # Function with only Optional[Protocol] param
    print(only_optional())
    print(only_optional(nums))
    print(only_optional(None))

    # Required + optional protocol in constructor
    mc = MixedContainer(nums)
    print(mc.count)
    mc2 = MixedContainer(nums, more)
    print(mc2.count)
    mc3 = MixedContainer(nums, None)
    print(mc3.count)

    # Generic Optional[Sequence[int]]
    print(sum_optional(nums))
    print(sum_optional(nums, more))
    print(sum_optional(nums, None))

main()
