from tpy import Int32
def total(*xs: Int32) -> Int32:
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
