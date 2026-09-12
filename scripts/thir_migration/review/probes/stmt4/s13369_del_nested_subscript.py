from tpy import int32
def f(d: dict[str, dict[str, int32]]) -> int32:
    del d['a']['b']
    return len(d)
def main() -> None:
    print(f({'a': {'b': 1}}))
main()
