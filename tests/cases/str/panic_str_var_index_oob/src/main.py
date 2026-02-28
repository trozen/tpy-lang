# String out-of-bounds via negative variable index should panic
from tpy import Int32

def main() -> None:
    s: str = "hi"
    i: Int32 = -10
    print(s[i])

main()
