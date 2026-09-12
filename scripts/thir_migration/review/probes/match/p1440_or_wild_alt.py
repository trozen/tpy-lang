from tpy import int32

def main() -> None:
    n: int32 = 3
    match n:
        case 1 | _:
            print("one-or-any")

main()
