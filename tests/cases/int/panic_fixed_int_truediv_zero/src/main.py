# Fixed-width int true division by zero panics
from tpy import Int32

def main() -> None:
    a: Int32 = Int32(5)
    b: Int32 = Int32(0)
    print(a / b)

main()
