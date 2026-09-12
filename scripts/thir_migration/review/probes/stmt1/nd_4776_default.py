from tpy import int32
def main() -> None:
    def f(x: int32 = 1) -> int32:
        return x
    print(f(1))
main()
