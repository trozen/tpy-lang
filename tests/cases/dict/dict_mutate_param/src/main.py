# Mutation through a by-reference dict parameter
from tpy import Int32

def insert(d: dict[str, Int32], key: str, val: Int32) -> None:
    d[key] = val

def main() -> None:
    d = {"a": 1}
    insert(d, "b", 2)
    print(d)

main()
