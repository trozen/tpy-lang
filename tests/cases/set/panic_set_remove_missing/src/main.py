# Panic: remove() on missing element raises KeyError
from tpy import int32

def main() -> None:
    s: set[int32] = {1, 2, 3}
    s.remove(99)

main()
