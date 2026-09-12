from tpy import int32
def f() -> int32:
    return 3
def main() -> None:
    if f():
        print("yes")
    n = 2
    if n + 1:
        print("sum")
main()
