# A dict (or set) LITERAL at a generic callee's container slot keeps
# rejecting: at a substituted slot the literal renders INLINE, and an inline
# prvalue cannot bind the mutated `ordered_map<..>&` parameter. The list
# literal has its own hoisting cell there; the dict and set literals do not.
from tpy import int32


def take_dict[T](w: T, d: dict[str, int32]) -> int32:
    d["z"] = 9
    return len(d)


def main() -> None:
    print(take_dict(1, {"b": 2}))  # tpyc: error(/not yet supported.*generic_arg_shape/)


main()
