# A walrus write to a `global` invalidates views borrowing that global exactly
# like the plain assignment does -- both spellings must warn, or the walrus form
# leaves a dangling view unreported.

from tpy import StrView

label = "hello world"


def plain() -> int:
    global label
    sv: StrView = label
    n = len(sv)
    label = "a much longer replacement string"  # tpyc: warning(/Mutation of 'label' while borrowed/)
    return n


def via_walrus() -> int:
    global label
    sv: StrView = label
    n = len(sv)
    m = len(label := "another replacement string")  # tpyc: warning(/Mutation of 'label' while borrowed/)
    return n + m


def main() -> None:
    print("plain:", plain())
    print("walrus:", via_walrus())
    print("label:", label)


main()
