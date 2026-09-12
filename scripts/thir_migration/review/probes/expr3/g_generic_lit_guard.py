from tpy import int32
def pick[T](a: T, b: T) -> T:
    return b

def main() -> None:
    k = 1
    match k:
        case 1 if pick(1, 2) == 2:
            print("a")
        case _:
            print("b")
main()
