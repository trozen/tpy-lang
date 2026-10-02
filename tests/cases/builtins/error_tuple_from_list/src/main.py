# tuple(xs) over a list is refused: a tuple's length is part of its type, so
# it cannot come from a sequence whose length is known only at run time
# (docs/LANGUAGE_FEATURES.md, the builtins section).
def main() -> None:
    xs = [3, 1, 2]
    t = tuple(xs)  # tpyc: error(/tuple\(\.\.\.\) cannot build a tuple from a sequence: a tuple has a fixed number of elements/)
    print(t)


main()
