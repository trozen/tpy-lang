# String out-of-bounds via negative variable index should panic
from tpy import int32

def main() -> None:
    s: str = "hi"
    i: int32 = -10
    print(s[i])

main()
