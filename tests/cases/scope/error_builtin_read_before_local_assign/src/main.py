# A builtin's name a function later assigns is that function's local for its
# whole body: calling it before the assignment is CPython's UnboundLocalError,
# never a call of the builtin.
from tpy import int32


def main() -> None:
    xs = [3, 1, 2]
    # The builtin the later `max = 0` makes local.
    print(max(xs))  # tpyc: error(/'max' is read before it is bound on line 11: a name bound anywhere in a function is local to the whole function, so this read does not see the builtin 'max'; rename one of them/)
    max = 0
    print(max)


main()
