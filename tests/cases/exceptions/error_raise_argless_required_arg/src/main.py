# An argless `raise E()` is checked against E's constructor like `E()`.
class E(Exception):
    def __init__(self, code: int) -> None:
        super().__init__("x")
        self.code = code


def main() -> None:
    raise E()  # tpyc: error(/'E\(\)' expects 1 argument\(s\), got 0/)


main()
