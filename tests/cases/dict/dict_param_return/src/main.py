# Dict as function parameter and return type (Own for by-value return)
from tpy import int32, Own

def make_dict() -> Own[dict[str, int32]]:
    d = {"x": 1, "y": 2}
    return d

def sum_values(d: dict[str, int32]) -> int32:
    total = 0
    for k in d:
        total = total + d[k]
    return total

def main() -> None:
    d = make_dict()
    print(d)
    print(sum_values(d))

main()
