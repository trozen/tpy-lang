from tpy import Int32
from container import Box

def main() -> Int32:
    b: Box[Int32] = Box(Int32(42))
    print(b.value)
    return Int32(0)

main()
