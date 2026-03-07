# Set as function parameter and return value
from tpy import Int32, Own

def make_set() -> Own[set[Int32]]:
    return {1, 2, 3}

def add_to_set(s: set[Int32], val: Int32) -> None:
    s.add(val)

def get_size(s: set[Int32]) -> Int32:
    return len(s)

def main() -> None:
    s = make_set()
    print(s)
    add_to_set(s, 4)
    print(s)
    print(get_size(s))

main()
