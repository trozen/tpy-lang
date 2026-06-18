# Guard: widening float arithmetic to the AnyFixedInt marker protocol must not
# over-match -- a user class (which does not extend it) is still rejected.
class P:
    def __init__(self, x: int):
        self.x = x


def main() -> None:
    f = 3.0
    p = P(1)
    print(f + p)  # tpyc: error(/Invalid operand types for '\+': float and P/)


main()
