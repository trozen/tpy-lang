from tpy import Char
def f(c: Char | None) -> bool:
    if c is not None:
        return c == 'x'
    return False
def main() -> None:
    print(f(Char('x')))
main()
