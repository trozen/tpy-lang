# Tuple unpacking into existing variables (reassignment)
from tpy import Int32

def get_pair() -> tuple[Int32, Int32]:
    return (Int32(10), Int32(20))

def main() -> None:
    a: Int32 = 0
    b: Int32 = 0
    print(a)
    print(b)
    a, b = get_pair()
    print(a)
    print(b)

main()
