# Set as function parameter and return value
from tpy import int32, Own

def make_set() -> Own[set[int32]]:
    return {1, 2, 3}

def add_to_set(s: set[int32], val: int32) -> None:
    s.add(val)

def get_size(s: set[int32]) -> int32:
    return len(s)

def main() -> None:
    s = make_set()
    print(s)
    add_to_set(s, 4)
    print(s)
    print(get_size(s))

main()
