from tpy import int32
def compute(x: int32) -> int32:
    return x * 2
def go(x: int32) -> int32:
    match (n := compute(x)):
        case 0:
            return 0
        case _:
            return n + 1
def main() -> None:
    print(go(2))
main()
