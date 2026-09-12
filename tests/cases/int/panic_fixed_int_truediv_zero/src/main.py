# Fixed-width int true division by zero panics
from tpy import int32

def main() -> None:
    a: int32 = int32(5)
    b: int32 = int32(0)
    print(a / b)

main()
