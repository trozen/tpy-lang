# `d.setdefault(key, default)`: the key is a lookup form, so a literal or a
# param passes as a view and the runtime builds the stored key only on a miss.
from tpy import Int32

def main() -> None:
    d: dict[str, Int32] = {"a": 1, "b": 2}
    print(d.setdefault("a", 99))   # 1 (exists)
    print(d.setdefault("c", 42))   # 42 (inserted)
    print(d["c"])                    # 42
    print(len(d))                    # 3

main()
