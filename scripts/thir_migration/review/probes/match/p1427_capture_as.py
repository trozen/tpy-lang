from tpy import Int32

def main() -> None:
    n: Int32 = 3
    match n:
        case x as y:
            print(x, y)

main()
