# Cycles using whole-module imports (`import a` rather than
# `from a import X`) work end-to-end: each module names the other
# through its module namespace and the call-site resolution uses
# module attribute access at body-sema time, by which point both
# modules have finalized declarations through the workspace-wide
# two-pass sema. A and B form a 2-cycle; main calls into both
# directions of the cycle.
import a
import b

def main() -> None:
    print(a.foo())     # a -> b.bar
    print(b.relay())   # b -> a.foo -> b.bar

main()
