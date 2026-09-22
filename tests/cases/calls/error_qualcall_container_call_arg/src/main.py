# The qualified-call family gained the comprehension cell
# (calls/qualcall_comprehension_arg) and nothing else: the everyday sibling --
# a container-returning CALL rvalue at the same slot -- has no cell in any
# family that reaches here and keeps rejecting.
from tpy import Own, int32


class Holder:
    @staticmethod
    def take_list(xs: list[int32]) -> int32:
        xs.append(99)
        return len(xs)


def mk() -> Own[list[int32]]:
    return [1, 2]


def main() -> None:
    print(Holder.take_list(mk()))  # tpyc: error(/not yet supported.*call_rvalue/)


main()
