# A nested def whose name matches a module-level function. The body`s const
# verdicts are looked up BY NAME, so the nested body would read the outer
# function`s param verdicts and could spell a param const-ness that is not its
# own -- the shape rejects rather than risk that.
from tpy import int32


def helper(x: int32) -> int32:
    return x + 1


def main() -> None:
    def helper(x: int32) -> int32:  # tpyc: error(/not yet supported.*nesteddef.name_collision/)
        return x + 2

    print(helper(1))


main()
