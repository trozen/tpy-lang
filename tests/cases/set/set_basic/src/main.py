# Basic set creation, add, len, print, contains
from tpy import Int32

def main() -> None:
    s: set[Int32] = {1, 2, 3}
    print(s)
    print(len(s))
    s.add(4)
    print(s)
    s.add(2)  # duplicate, no effect
    print(s)
    print(len(s))

main()
