# Test returning narrowed optional values in various contexts
from typing import Optional

def unwrap_or(x: Optional[int], fallback: int) -> int:
    if x is not None:
        return x
    return fallback

def first_positive(nums: list[int]) -> Optional[int]:
    for n in nums:
        if n > 0:
            return n
    return None

def main() -> None:
    print(unwrap_or(first_positive([1, 2, 3]), 0))
    print(unwrap_or(first_positive([-1, -2]), 0))
    print(unwrap_or(None, 42))

main()
