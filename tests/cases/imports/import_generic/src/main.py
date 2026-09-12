from tpy import int32
from container import Box

def main() -> int32:
    b: Box[int32] = Box(int32(42))
    print(b.value)
    return int32(0)

main()
