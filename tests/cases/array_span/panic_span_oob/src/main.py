# Out-of-bounds span access panics at runtime.
from tpy import int32, Span, Array

def main() -> None:
    arr: Array[int32, 2] = [10, 20]
    s: Span[int32] = arr
    print(s[5])

main()
