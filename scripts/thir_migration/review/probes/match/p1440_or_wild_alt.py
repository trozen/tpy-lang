from tpy import Int32

def main() -> None:
    n: Int32 = 3
    match n:
        case 1 | _:
            print("one-or-any")

main()
