# Test d.setdefault(key, default)
from tpy import Int32

def main() -> None:
    d: dict[str, Int32] = {"a": 1, "b": 2}
    print(d.setdefault("a", 99))   # 1 (exists)
    print(d.setdefault("c", 42))   # 42 (inserted)
    print(d["c"])                    # 42
    print(len(d))                    # 3

main()
