# `list([...])` over a STR element literal: the builtin-generic call form
# is one the call lowering has no arm for (the owned decl route asks it
# before the instantiation arm would). A shipped limitation, not a rule:
# CPython builds the list, and the bare literal `["x", "y"]` compiles
# (BUGS.md#list-ctor-over-str-literal-unlowered).
def main() -> None:
    a = list(["x", "y"])  # tpyc: error(/expr\.call:call\.special\.call_type\.builtin_generic/)
    print(a[0])


main()
