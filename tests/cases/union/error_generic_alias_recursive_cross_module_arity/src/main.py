# Arity mismatch on a *qualified* cross-module generic recursive alias use:
# treelib.Tree takes one type parameter, so `treelib.Tree[int, str]` is
# rejected at parse-resolution. Guards the cross-module use-site arity check
# (and that the diagnostic names the short alias, not the dotted spelling).
import treelib


def main() -> None:
    t: treelib.Tree[int, str] = 5  # tpyc: error(/Type alias 'Tree' takes 1 type argument, got 2/)
    print(t)


main()
