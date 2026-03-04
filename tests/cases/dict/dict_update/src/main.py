# Test d.update(other) -- merge other dict into d
from tpy import Int32

def main() -> None:
    d: dict[str, Int32] = {"a": 1, "b": 2}
    d2: dict[str, Int32] = {"b": 20, "c": 30}
    d.update(d2)
    for k in d:
        print(k, d[k])
    print(len(d))              # 3

    # Literal argument -- type inferred from receiver
    d3: dict[str, Int32] = {"x": 10}
    d3.update({"x": 99, "y": 20})
    for k in d3:
        print(k, d3[k])

main()
