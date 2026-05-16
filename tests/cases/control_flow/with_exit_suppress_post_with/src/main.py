# Regression for the liveness change: a `-> int` function whose with-body
# always raises but whose CM may suppress. The Python body terminates
# (`raise`), but the with cannot be considered terminating -- control may
# fall through past the with on the suppressed path. The trailing
# `return -1` is the reachable result on that path. Pre-liveness-fix the
# function was treated as terminating-on-body, leaving the post-with
# return at risk of being treated as dead.


class Suppressor:
    def __enter__(self) -> int:
        return 1

    def __exit__(self, exc_type, exc_val, exc_tb) -> bool:
        return exc_val is not None  # suppress any caught exception


def maybe_neg_one(do_raise: bool) -> int:
    with Suppressor():
        if do_raise:
            raise ValueError("boom")
        return 100
    return -1


def main() -> None:
    print(maybe_neg_one(False))   # 100 -- body returns 100 through finally chain
    print(maybe_neg_one(True))    # -1  -- CM suppresses, trailing return fires


main()
