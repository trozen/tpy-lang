from tpy import Int32
def main() -> None:
    def total(xs: list[Int32]) -> Int32:
        t = 0
        for x in xs:
            t += x
        return t
    print(total([1, 2, 3]))
main()
