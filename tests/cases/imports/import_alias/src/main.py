from tpy import int32
from utils import Point as Pt, MAX_VALUE as MAX, add as sum_nums

def main() -> int32:
    p = Pt(int32(3), int32(4))
    print(p.x)
    print(MAX)
    print(sum_nums(int32(10), int32(20)))
    return int32(0)

main()
