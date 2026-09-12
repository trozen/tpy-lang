# Test bounded generic function with user-defined type
# The user type implements __len__ so satisfies Sized
from typing import Sized
from tpy import int32

class MyContainer:
    data: list[int32]

    def __init__(self, items: list[int32]) -> None:
        self.data = items

    def __len__(self) -> int32:
        return len(self.data)

def get_length[T: Sized](item: T) -> int32:
    # Note: Can't call len(item) here yet - returning fixed value
    # This tests that the bound is validated during inference
    return 42

def main() -> None:
    c = MyContainer([1, 2, 3, 4, 5])
    # MyContainer satisfies Sized, so inference should work
    result = get_length(c)
    print(result)

main()
