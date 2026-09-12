from tpy import int32
def main() -> None:
    c = True
    b = b'abc'
    x: bytes = b if c else b'zz'
    print(len(x))
main()
