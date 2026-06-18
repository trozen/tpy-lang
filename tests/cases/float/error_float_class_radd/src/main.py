# Guard (reverse order): the __radd__ path must not over-match either -- a user
# class on the left of a float is rejected, mirroring the forward-order guard.
class P:
    def __init__(self, x: int):
        self.x = x


def main() -> None:
    f = 3.0
    p = P(1)
    print(p + f)  # tpyc: error(/Invalid operand types for '\+': P and float/)


main()
