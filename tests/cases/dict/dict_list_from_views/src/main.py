# Test list() construction from dict views (keys, values, items)
from tpy import Int32

def main() -> None:
    d: dict[str, Int32] = {"a": 1, "b": 2, "c": 3}

    keys = list(d.keys())
    print(keys)

    vals = list(d.values())
    print(vals)

    items = list(d.items())
    print(len(items))
    k, v = items[0]
    print(k, v)

main()
