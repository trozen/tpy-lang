# Dict variable reassignment from literal
from tpy import int32

def main() -> None:
    d: dict[str, int] = {"a": 1}
    print(d["a"])
    d = {"b": 2, "c": 3}
    print(d["b"], d["c"])

    # Union dict reassignment
    d2: dict[str, int32 | str] = {"x": 1, "y": "hello"}
    d2 = {"z": "world"}
    v = d2["z"]
    if isinstance(v, str):
        print(v)

main()
