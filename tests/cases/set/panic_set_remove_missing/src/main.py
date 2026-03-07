# Panic: remove() on missing element raises KeyError
from tpy import Int32

def main() -> None:
    s: set[Int32] = {1, 2, 3}
    s.remove(99)

main()
