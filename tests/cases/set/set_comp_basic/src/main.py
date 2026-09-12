# Set comprehension: basic cases (range, list, dedup)
from tpy import int32

def main() -> None:
    # Range to set
    squares: set[int32] = {x * x for x in range(5)}
    for v in squares:
        print(v)

    # List to set (dedup)
    items: list[int32] = [1, 2, 2, 3, 3, 3]
    unique: set[int32] = {x for x in items}
    print(len(unique))

    # String set from list
    names: list[str] = ["alice", "bob", "alice", "charlie"]
    name_set: set[str] = {n for n in names}
    print(len(name_set))

    # 2-arg range
    r2: set[int32] = {x for x in range(3, 7)}
    for v in r2:
        print(v)

main()
