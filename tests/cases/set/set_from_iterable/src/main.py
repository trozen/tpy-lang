# Construct set from list (iterable)
from tpy import int32

def main() -> None:
    items: list[int32] = [10, 20, 30, 20, 10]
    s: set[int32] = set(items)
    print(s)
    print(len(s))

main()
