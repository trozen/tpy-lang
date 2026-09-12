# divmod() panics on division by zero
from tpy import int32

def main() -> None:
    a: int32 = int32(10)
    b: int32 = int32(0)
    q, r = divmod(a, b)
    print(q)

main()
