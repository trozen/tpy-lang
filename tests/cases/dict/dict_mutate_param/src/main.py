# Mutation through a by-reference dict parameter
from tpy import int32

def insert(d: dict[str, int32], key: str, val: int32) -> None:
    d[key] = val

def main() -> None:
    d = {"a": 1}
    insert(d, "b", 2)
    print(d)

main()
