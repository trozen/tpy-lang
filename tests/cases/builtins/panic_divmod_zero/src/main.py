# divmod() panics on division by zero
from tpy import Int32

def main() -> None:
    a: Int32 = Int32(10)
    b: Int32 = Int32(0)
    q, r = divmod(a, b)
    print(q)

main()
