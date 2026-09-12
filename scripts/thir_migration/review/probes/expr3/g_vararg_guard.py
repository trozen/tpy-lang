from tpy import int32
def total(*xs: int32) -> int32:
    s = 0
    for x in xs:
        s += x
    return s

def main() -> None:
    k = 1
    match k:
        case 1 if total(1, 2, 3) > 0:
            print("a")
        case _:
            print("b")
main()
