from tpy import int32

def main() -> None:
    n: int32 = 3
    match n:
        case x as y:
            print(x, y)

main()
