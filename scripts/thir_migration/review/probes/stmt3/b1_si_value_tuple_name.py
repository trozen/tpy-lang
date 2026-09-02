from tpy import Int32
def make() -> tuple[str, str]:
    return ('a', 'b')
def main() -> None:
    d: dict[str, tuple[str, str]] = {}
    d['k'] = make()
    print(len(d))
main()
