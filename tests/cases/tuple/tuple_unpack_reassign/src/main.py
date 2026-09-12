# Tuple unpacking into existing variables (reassignment)
from tpy import int32

def get_pair() -> tuple[int32, int32]:
    return (int32(10), int32(20))

def main() -> None:
    a: int32 = 0
    b: int32 = 0
    print(a)
    print(b)
    a, b = get_pair()
    print(a)
    print(b)

main()
