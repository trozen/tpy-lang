from tpy import int32
def main() -> None:
    a = "ab"
    b = "cd"
    c = True
    n = (a + b).upper()
    m = (a if c else b).upper()
    print(n, m)
main()
