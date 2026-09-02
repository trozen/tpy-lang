def f(s: str) -> None:
    print(1, 2, sep=s + '-')
def main() -> None:
    f(',')
main()
