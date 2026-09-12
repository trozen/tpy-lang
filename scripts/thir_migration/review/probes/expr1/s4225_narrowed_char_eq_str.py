from tpy import char
def f(c: char | None) -> bool:
    if c is not None:
        return c == 'x'
    return False
def main() -> None:
    print(f(char('x')))
main()
