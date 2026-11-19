# Generator expressions: each is a frame MIR lowers as a generator's (`<genexpr>`
# verdicts in mir.txt; a `mir(...)` annotation cannot name one).
from tpy import int32


# genexpr_range: over a range
def squares(n: int32) -> int32:
    return sum(i * i for i in range(n))


# genexpr_bytes: over a bytes parameter, which the frame copies
def byte_sum(data: bytes) -> int:
    return sum(b - 96 for b in data)


# genexpr_param_list: over the caller's list: its cursor is open across each pull
def total(xs: list[int32]) -> int32:
    return sum(x * 2 for x in xs)


# genexpr_filter: an `if` clause
def evens(n: int32) -> int32:
    return sum(i for i in range(n) if i % 2 == 0)


# genexpr_two_clauses: two `for` clauses
def table(n: int32) -> int32:
    return sum(i * j for i in range(n) for j in range(3))


def main() -> None:
    print("genexpr_range:", squares(4))
    print("genexpr_bytes:", byte_sum(b"abc"))
    print("genexpr_param_list:", total([1, 2, 3]))
    print("genexpr_filter:", evens(6))
    print("genexpr_two_clauses:", table(3))
