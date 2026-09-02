from tpy import Int32
G = 5
def f(n: Int32) -> Int32:
    if n > 0:
        return G
    G = 7
    return G
def main() -> None:
    print(f(1))
main()
