from tpy import int32
def f() -> None:
    kw: dict[str, str] = {'sep': ','}
    print(1, 2, **kw)
def main() -> None:
    f()
main()
