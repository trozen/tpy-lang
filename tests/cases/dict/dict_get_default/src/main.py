# Test d.get(key, default) -- returns value if present, default otherwise
from tpy import Int32

def main() -> None:
    d: dict[str, Int32] = {"a": 1, "b": 2}
    print(d.get("a", 99))     # 1
    print(d.get("z", 99))     # 99
    print(d.get("b", 0))      # 2

main()
