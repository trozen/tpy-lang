# Bare integer literals as dict keys and values
from tpy import int32

def main() -> None:
    d = {1: "a", 2: "b"}
    print(d[1])
    d2 = {"x": 1, "y": 2}
    print(d2["x"])
    # Mixed: int32 keys with bare int values
    d3 = {int32(10): 100, int32(20): 200}
    print(d3[int32(10)])

main()
