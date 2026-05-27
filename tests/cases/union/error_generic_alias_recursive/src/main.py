# Generic recursive type aliases stay rejected in Phase 1 of the
# rollout. Phase 2 will land the templated wrapper struct.
from tpy import Int32

# tpyc: error(/Generic recursive type aliases are not yet supported/)
type Tree[T] = T | list[Tree[T]]


def main() -> None:
    print("never reached")


main()
