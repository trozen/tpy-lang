from tpy import Int32

def f(n: Int32, u: Int32 | str) -> None:
    if isinstance(u, Int32):
        match n:
            case _ as u:
                print(u)

def main() -> None:
    f(1, 2)

main()
