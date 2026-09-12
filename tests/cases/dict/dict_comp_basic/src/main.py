# Dict comprehension: basic cases (range, list, dict rebuild)
from tpy import int32

def main() -> None:
    # Range to dict
    squares: dict[int32, int32] = {x: x * x for x in range(5)}
    for k in squares:
        print(k, squares[k])

    # List to dict
    names: list[str] = ["alice", "bob", "charlie"]
    name_lens: dict[str, int32] = {n: len(n) for n in names}
    print(name_lens["alice"])
    print(name_lens["bob"])
    print(name_lens["charlie"])

    # Rebuild dict (identity)
    src: dict[str, int32] = {"a": 1, "b": 2}
    copy: dict[str, int32] = {k: v for k, v in src.items()}
    print(copy["a"])
    print(copy["b"])

    # 2-arg range
    r2: dict[int32, int32] = {x: x + 10 for x in range(2, 5)}
    for k in r2:
        print(k, r2[k])

main()
