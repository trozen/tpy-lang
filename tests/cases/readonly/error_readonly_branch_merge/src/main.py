# After if/else where only one branch reassigns a readonly alias,
# the merged result stays readonly (conservative).
from tpy import int32, readonly

class Point:
    x: int32

    def __init__(self, x: int32) -> None:
        self.x = x

    def set_x(self, v: int32) -> None:
        self.x = v

def bad_branch(p: readonly[Point], flag: bool) -> None:
    alias = p
    if flag:
        alias = Point(int32(0))
    alias.set_x(int32(99))  # tpyc: error(/readonly/)

def main() -> None:
    p = Point(int32(5))
    bad_branch(p, False)
    print(p.x)

main()
