# Refuses valid Python by the rule "Numeric widening across reassignments",
# Annotated (docs/LANGUAGE_FEATURES.md): an annotation declares the
# variable's one slot, so a sibling arm's earlier binding of an
# incompatible value is checked against it.
def main(c: bool) -> None:
    if c:
        x = "s"  # tpyc: error(/Assignment to 'x' is incompatible with later annotation 'int' at line 9/)
    else:
        x: int = 1
    print(x)


main(True)
