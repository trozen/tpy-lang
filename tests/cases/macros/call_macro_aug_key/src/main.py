# A call macro's expansion as an augmented-assignment key: the hold proof
# reads the expansion, which runs nothing observable, so the key is re-read in
# place -- never a held view over the concatenation's dying temporary.
# Also: a call macro's expansion keeps the analysis of the arguments it
# embeds, and `ast.clone` copies.
from aug_macros import clone_edit, trimmed, twice
from tpy import int32


def f(x: str) -> str:
    return x


def main() -> None:
    s = "a-key-longer-than-the-small-string-buffer"
    d = {s: 0}
    # the subject: `trimmed(s)` expands to `(" " + s).strip()`
    d[trimmed(s)] += 1  # tpyc: ok
    print("macro_key:", d[s])
    # An argument embedded twice is the analyzed node at both places, its
    # element read converted once for the `str` parameter. Evaluating it
    # at both places (`f` runs twice) is the filed defect
    # BUGS.md#call-macro-arg-evaluated-per-field, not the intended result.
    print("twice:", twice(f(s[0])))  # tpyc: ok
    # Editing a clone leaves the argument as written.
    r: tuple[int32, int32] = clone_edit(7)  # tpyc: ok
    print("clone:", r)


main()
