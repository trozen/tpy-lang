from tpy import Int32
def f(d: dict[str, Int32] | None) -> Int32:
    if d is not None:
        del d['a']
        return len(d)
    return 0
def main() -> None:
    print(f({'a': 1}))
main()
