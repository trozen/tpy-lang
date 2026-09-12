from tpy import int32

def f(n: int32, u: int32 | str) -> None:
    if isinstance(u, int32):
        match n:
            case _ as u:
                print(u)

def main() -> None:
    f(1, 2)

main()
