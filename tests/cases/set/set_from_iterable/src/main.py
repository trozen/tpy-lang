# Construct set from list (iterable)
from tpy import Int32

def main() -> None:
    items: list[Int32] = [10, 20, 30, 20, 10]
    s: set[Int32] = set(items)
    print(s)
    print(len(s))

main()
