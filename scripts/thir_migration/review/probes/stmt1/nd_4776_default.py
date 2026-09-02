from tpy import Int32
def main() -> None:
    def f(x: Int32 = 1) -> Int32:
        return x
    print(f(1))
main()
