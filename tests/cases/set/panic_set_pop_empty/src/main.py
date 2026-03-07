# Panic: pop() on empty set raises KeyError
from tpy import Int32

def main() -> None:
    s: set[Int32] = set()
    s.pop()

main()
