from tpy import int32
from utils import add, Point

def main() -> int32:
    p = Point(int32(1), int32(2))
    result = add(p.x, p.y)
    print(result)
    return int32(0)

main()
