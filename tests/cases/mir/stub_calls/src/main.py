# MIR verdicts for calls to stdlib stubs: a declared contract (@pure / transient=True)
# admits the call and summarizes the caller; `len` dispatching a record's __len__ refuses.
import math
import time
from tpy import int32

G: str = "glob"


class Rec:
    n: int32

    # constructor: a scalar field write
    def __init__(self, n: int32):  # tpyc: mir(covered)
        self.n = n

    def __len__(self) -> int32:
        return self.n

    # method: `len` on a str parameter, lent to the @pure stub
    def label_len(self, s: str) -> int32:  # tpyc: mir(covered)
        return len(s)


# free function: `len` is @pure; its protocol parameter binds the str, lent for the call
def lent_len(s: str) -> int32:  # tpyc: mir(covered) mir_summary(known)
    return len(s)


# free function: a global argument is lent through its handle
def global_len() -> int32:  # tpyc: mir(covered) mir_summary(known)
    return len(G)


# free function: `time.time` is bound transient=True and takes no arguments
def clock() -> float:  # tpyc: mir(covered) mir_summary(known)
    return time.time()


# free function: a pure initializer lends nothing, so its BigInt result is fresh
def construct(x: float) -> int:  # tpyc: mir(covered) mir_summary(known)
    return int(x)


# free function: the `min` result may borrow either argument, so the local copies it
def smaller(a: int, b: int) -> int:  # tpyc: mir(covered) mir_summary(known)
    x = min(a, b)
    return x


# free function: as an operand the borrowed `min` result is read in place
def smaller_is(a: int, b: int) -> bool:  # tpyc: mir(covered) mir_summary(known)
    return min(a, b) == a


# free function: the result may borrow the sum's temporary
def smaller_sum(a: int, b: int) -> int:  # tpyc: mir(covered) mir_summary(known)
    x = min(a + b, b)
    return x


# free function: the int32 and BigInt overloads of one stub in one body
def overloads(a: int, b: int, i: int32, j: int32) -> bool:  # tpyc: mir(covered) mir_summary(known)
    return min(i, j) == 1 and min(a, b) == a


# free function: two calls of one stub share one summary
def twice(x: float) -> float:  # tpyc: mir(covered) mir_summary(known)
    y = math.log(x)
    return math.log(y)


# free function: two different stubs in one body
def both(s: str, a: int, b: int) -> int32:  # tpyc: mir(covered) mir_summary(known)
    x = min(a, b)
    return len(s)


# free function: `len` on a record dispatches the record's own __len__
def record_len(r: Rec) -> int32:  # tpyc: mir(uncovered /^stub protocol argument is not a builtin leaf$/)
    return len(r)


def main() -> None:
    print("label_len:", Rec(0).label_len("four"))
    print("lent_len:", lent_len("abc"))
    print("global_len:", global_len())
    print("clock:", clock() > 0)
    print("construct:", construct(3.7))
    print("smaller:", smaller(7, 5))
    print("smaller_is:", smaller_is(3, 5))
    print("smaller_sum:", smaller_sum(3, 5))
    print("overloads:", overloads(3, 5, 1, 2))
    print("twice:", f"{twice(100.0):.6f}")
    print("both:", both("hi", 12, 40))
    print("record_len:", record_len(Rec(4)))


main()
