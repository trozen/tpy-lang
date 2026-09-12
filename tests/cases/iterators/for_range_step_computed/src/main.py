# A 3-arg range() whose step is a COMPUTED expression (not a literal or a
# bare name): the step is captured once into a temp, so an arbitrary
# expression is evaluated exactly once, as Python does.
from tpy import int32


def stride(n: int32) -> int32:
    return n * 2


class Grid:
    step: int32

    def __init__(self, step: int32):
        self.step = step


def fixed_steps(end: int32) -> int32:
    aux = 7
    total = 0
    # A binop step.
    for k in range(aux, end, 2 * aux):
        total += k
    # A call step.
    for k in range(0, end, stride(3)):
        total += k
    g = Grid(5)
    # A field-read step.
    for k in range(0, end, g.step):
        total += k
    # A negated binop step, counting down.
    for k in range(end, 0, -(2 * aux)):
        total += k
    # A ternary step.
    for k in range(0, end, aux if end > 3 else 1):
        total += k
    return total


def bigint_step(end: int) -> int:
    aux: int = 7
    total: int = 0
    # A BigInt counter with a computed step: same capture, no overflow check
    # (an arbitrary-precision counter cannot overflow).
    for k in range(aux, end, 2 * aux):
        total += k
    return total


def main():
    print(fixed_steps(60))
    print(bigint_step(60))


main()
