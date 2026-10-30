# A literal unpack whose element is a fresh concatenation binds a view
# target to a temporary that dies at the end of the statement: refused,
# as its twin `a = p + "x"` is.
from tpy import int32, StrView


def last(p: str) -> int32:
    a: StrView = ""
    k = 0
    for i in range(3):
        a, k = (p + "x", i)  # tpyc: error(/Cannot bind StrView 'a' to a temporary view source/)
    return len(a) + k


def main() -> None:
    print(last("p"))


main()
