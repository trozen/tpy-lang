# Ternary expression: dangling reference when returning temporary record
from typing import Optional

class Point:
    def __init__(self, x: int, y: int) -> None:
        self.x = x
        self.y = y

def with_default(p: Optional[Point]) -> Point:
    return p if p is not None else Point(0, 0)  # tpyc: error(/Cannot return local or temporary/)

def main() -> None:
    pass

main()
