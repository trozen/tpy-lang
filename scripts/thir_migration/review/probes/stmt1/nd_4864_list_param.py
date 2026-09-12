from tpy import int32
def main() -> None:
    def total(xs: list[int32]) -> int32:
        t = 0
        for x in xs:
            t += x
        return t
    print(total([1, 2, 3]))
main()
