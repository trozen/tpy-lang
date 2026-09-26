# An int-seeded local rebound to a float in one branch is refused: CPython keeps
# the int when the branch does not run, and a local has one numeric type.

def pick(c: bool) -> None:
    x = 1
    if c:
        x = 2.5  # tpyc: error(/'x' is bound to int at line 5 and to float here.*write 1\.0 instead of 1, or annotate x: float/)
    print(x)

pick(False)
