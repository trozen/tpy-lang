# After if/else where only one branch reassigns a readonly alias,
# the merged result stays readonly (conservative).
from tpy import Int32, readonly

class Point:
    x: Int32

    def __init__(self, x: Int32) -> None:
        self.x = x

    def set_x(self, v: Int32) -> None:
        self.x = v

def bad_branch(p: readonly[Point], flag: bool) -> None:
    alias = p
    if flag:
        alias = Point(Int32(0))
    alias.set_x(Int32(99))  # tpyc: error(/readonly/)

def main() -> None:
    p = Point(Int32(5))
    bad_branch(p, False)
    print(p.x)

main()
