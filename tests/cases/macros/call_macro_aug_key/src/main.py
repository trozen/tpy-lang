# A call macro's expansion as an augmented-assignment key: the hold proof
# reads the expansion, which runs nothing observable, so the key is re-read in
# place -- never a held view over the concatenation's dying temporary.
from aug_macros import trimmed


def main() -> None:
    s = "a-key-longer-than-the-small-string-buffer"
    d = {s: 0}
    # the subject: `trimmed(s)` expands to `(" " + s).strip()`
    d[trimmed(s)] += 1  # tpyc: ok
    print("macro_key:", d[s])


main()
