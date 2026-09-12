# Test ord() builtin -- inverse of chr()
from tpy import char, int32

def main() -> None:
    # Basic ASCII
    c: char = chr(65)
    print(ord(c))

    # Lowercase letter
    d: char = chr(122)
    print(ord(d))

    # Null character
    zero: char = chr(0)
    print(ord(zero))

    # ord/chr roundtrip
    n: int32 = 97
    print(ord(chr(n)))

main()
