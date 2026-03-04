# Out-of-bounds span access panics at runtime.
from tpy import Int32, Span, Array

def main() -> None:
    arr: Array[Int32, 2] = [10, 20]
    s: Span[Int32] = arr
    print(s[5])

main()
