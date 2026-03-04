# Test ord() builtin -- inverse of chr()
from tpy import Char, Int32

def main() -> None:
    # Basic ASCII
    c: Char = chr(65)
    print(ord(c))

    # Lowercase letter
    d: Char = chr(122)
    print(ord(d))

    # Null character
    zero: Char = chr(0)
    print(ord(zero))

    # ord/chr roundtrip
    n: Int32 = 97
    print(ord(chr(n)))

main()
