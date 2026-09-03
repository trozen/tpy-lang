# A GUARDED or-group with a wildcard alternative keeps rejecting: a label-less
# group lands in the switch `default:` regardless of source position, so the
# later `case 2:` would win over it (BUGS.md#guarded-wildcard-switch-default).
from tpy import Int32


def f(n: Int32, flag: bool) -> str:
    # The guard is what makes the group refutable -- and what the switch
    # default cannot express.
    match n:  # tpyc: error(/stmt\.match/)
        case 1 | _ if flag:
            return "guarded"
        case 2:
            return "two"
    return "other"


def main() -> None:
    print(f(2, True))


main()
