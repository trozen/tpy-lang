from tpy import Int32

def main() -> None:
    n: Int32 = 1
    s = "x"
    xs = [1, 2]
    match n:
        case 1 if s:
            print("s")
        case 2 if xs:
            print("xs")
        case 3 if n:
            print("n")
        case _:
            print("o")

main()
