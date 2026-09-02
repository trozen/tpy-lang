from tpy import Int32
def compute(x: Int32) -> Int32:
    return x * 2
def go(x: Int32) -> Int32:
    match (n := compute(x)):
        case 0:
            return 0
        case _:
            return n + 1
def main() -> None:
    print(go(2))
main()
