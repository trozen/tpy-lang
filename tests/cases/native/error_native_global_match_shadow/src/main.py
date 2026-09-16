# The match-arm sibling of native_global_shadow_branch_first: a local shadowing
# a native global, first bound in the arms of a match. The match ladder keeps
# its own predecl fence over the module's native-global NAMES, so this shape
# still rejects while the if / try / generator / async ones compile.
# BUGS.md#match-hoist-asks-native-global-map.
from tpy.extern import native_global
from tpy import int32

score: int32 = native_global("engine::score")


def pick(n: int32) -> int32:
    match n:  # tpyc: error(/not yet supported/)
        case 1:
            score = 5
        case _:
            score = 6
    return score


def main() -> None:
    print(pick(1))


main()
