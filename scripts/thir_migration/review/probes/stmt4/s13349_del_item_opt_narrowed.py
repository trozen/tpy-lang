from tpy import int32
def f(d: dict[str, int32] | None) -> int32:
    if d is not None:
        del d['a']
        return len(d)
    return 0
def main() -> None:
    print(f({'a': 1}))
main()
