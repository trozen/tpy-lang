# An EVALUATED sep= source is admitted only over inert arguments: the chain
# runs an inline argument AFTER the hoisted separator, while CPython evaluates
# every positional argument first (BUGS.md#subexpression-right-to-left-eval),
# so a call-valued argument beside it keeps rejecting rather than reordering
# two side effects.
def tag(s: str) -> str:
    print("eval", s)
    return s


def main() -> None:
    d = "-"
    print(tag("a"), tag("b"), sep=d + "|")  # tpyc: error(/not yet supported/)


main()
