from tpy import int32
G = 5
def f(n: int32) -> int32:
    if n > 0:
        return G
    G = 7
    return G
def main() -> None:
    print(f(1))
main()
