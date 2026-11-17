# t1 defines A only after its imports: fine, since main.py imports t2 first,
# and t2's star import runs t1 to completion.
import t2
import t3


class A:
    def __init__(self, n: int) -> None:
        self.n = n


def main() -> None:
    a = A(1)
    t2.bump(a)
    # The same object after the call: t2 borrowed it.
    print("t1:", a.n, t3.three().k)
