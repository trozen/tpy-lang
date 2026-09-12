# Basic set creation, add, len, print, contains
from tpy import int32

def main() -> None:
    s: set[int32] = {1, 2, 3}
    print(s)
    print(len(s))
    s.add(4)
    print(s)
    s.add(2)  # duplicate, no effect
    print(s)
    print(len(s))

main()
