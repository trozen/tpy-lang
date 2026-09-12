# Wrong default type in get(key, default) should error
from tpy import int32

def main() -> None:
    d: dict[str, int32] = {"a": 1}
    print(d.get("a", "wrong"))  # tpyc: error(/No matching overload for 'get'/)

main()
