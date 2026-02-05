from tpy import Int32
from utils import Point as Pt, MAX_VALUE as MAX, add as sum_nums

def main() -> Int32:
    p = Pt(Int32(3), Int32(4))
    print(p.x)
    print(MAX)
    print(sum_nums(Int32(10), Int32(20)))
    return Int32(0)

main()
