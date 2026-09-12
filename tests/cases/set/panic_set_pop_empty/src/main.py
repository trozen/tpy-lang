# Panic: pop() on empty set raises KeyError
from tpy import int32

def main() -> None:
    s: set[int32] = set()
    s.pop()

main()
