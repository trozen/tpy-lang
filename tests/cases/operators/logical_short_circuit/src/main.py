# and/or short-circuit: the unchosen operand is never evaluated, so a fallible
# or side-effecting right operand does not run (matching CPython).


def or_skips_fallible(c: list[int], d: list[int]) -> int:
    # c[0] truthy -> d[0] (out of range) must NOT be evaluated
    return c[0] or d[0]


def and_skips_fallible(c: list[int], d: list[int]) -> int:
    # c[0] falsy (0) -> d[0] (out of range) must NOT be evaluated
    return c[0] and d[0]


def logged(log: list[int], v: int) -> int:
    log.append(v)  # observable side effect: runs only if the operand is evaluated
    return v


def or_skips_side_effect(log: list[int]) -> int:
    z = 7 or logged(log, 1)  # 7 truthy -> logged() must NOT run
    return z


def and_skips_side_effect(log: list[int]) -> int:
    z = 0 and logged(log, 1)  # 0 falsy -> logged() must NOT run
    return z


def and_runs_rhs_when_truthy(log: list[int]) -> int:
    z = 7 and logged(log, 9)  # 7 truthy -> `and` returns RHS, logged() DOES run
    return z


def chained_or_skips_tail(c: list[int]) -> int:
    # first truthy operand wins; the rest (incl. out-of-range c[1]) not evaluated
    return c[0] or c[1] or c[1]


def chained_and_skips_tail(c: list[int]) -> int:
    # first falsy operand wins; the rest (incl. out-of-range c[1]) not evaluated
    return c[0] and c[1] and c[1]


def main() -> None:
    print(or_skips_fallible([5], []))       # 5 (no IndexError)
    print(and_skips_fallible([0], []))      # 0 (no IndexError)

    log1: list[int] = []
    print(or_skips_side_effect(log1), len(log1))   # 7 0  (RHS skipped)
    log2: list[int] = []
    print(and_skips_side_effect(log2), len(log2))  # 0 0  (RHS skipped)
    log3: list[int] = []
    print(and_runs_rhs_when_truthy(log3), len(log3))  # 9 1  (RHS ran)

    print(chained_or_skips_tail([3]))       # 3 (no IndexError on c[1])
    print(chained_and_skips_tail([0]))      # 0 (no IndexError on c[1])


main()
