from tpy import Int32
def f() -> Int32:
    return 3
def main() -> None:
    if f():
        print("yes")
    n = 2
    if n + 1:
        print("sum")
main()
