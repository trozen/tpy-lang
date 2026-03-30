# Star import from a user module (functions + classes)
from tpy import Int32
from utils import *

def main() -> Int32:
    p = Point(Int32(1), Int32(2))
    result = add(p.x, p.y)
    print(result)
    return Int32(0)

main()
