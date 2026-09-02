from tpy import Int32
def f(d: dict[str, dict[str, Int32]]) -> Int32:
    del d['a']['b']
    return len(d)
def main() -> None:
    print(f({'a': {'b': 1}}))
main()
