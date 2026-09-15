# A nested def whose name matches a module-level function or a method of the
# enclosing record. The lambda shadows the outer name for the rest of the body,
# exactly as Python rebinds it; the list params are mutated and re-read so a
# copy at the lambda boundary would show up in the output.
from typing import Callable

from tpy import int32


def tally(xs: list[int32]) -> int32:
    total = 0
    for v in xs:
        total += v
    return total


class Counter:
    n: int32

    def __init__(self) -> None:
        self.n = 3

    def tally(self, xs: list[int32]) -> int32:
        # method: the nested def takes the enclosing record's own method name
        def tally(ys: list[int32]) -> int32:  # tpyc: ok
            ys.append(self.n)
            return len(ys)

        return tally(xs)


def free_position() -> None:
    # free function: the nested def takes the module-level function's name
    def tally(xs: list[int32]) -> int32:  # tpyc: ok
        xs.append(9)
        return xs[0]

    data = [1, 2]
    print("free:", tally(data), data)


def closure_position() -> None:
    bump = 10

    # closure: a shadowing name that also captures an outer local
    def tally(xs: list[int32]) -> int32:  # tpyc: ok
        xs.append(bump)
        return len(xs)

    data = [4]
    print("closure:", tally(data), data)


def own_param_position() -> None:
    # the nested def's own PARAM owns the name inside its body, so the read is
    # the param and not the shadowed module function
    def tally(tally: list[int32]) -> int32:  # tpyc: ok
        tally.append(7)
        return len(tally)

    data = [1]
    print("own_param:", tally(data), data)


def own_local_position() -> None:
    # the nested def BINDS its own name in its body: Python makes the name
    # local for the whole nested function, so no read there reaches the
    # module function either
    def tally(xs: list[int32]) -> int32:  # tpyc: ok
        xs.append(8)
        tally = len(xs)
        return tally

    data = [2]
    print("own_local:", tally(data), data)


def sibling_after_position() -> None:
    # a SIBLING nested def declared after the shadowing one resolves to it,
    # the way Python does -- only a sibling declared BEFORE it diverges
    # (nested_def/error_name_collision_sibling_def)
    def tally(xs: list[int32]) -> int32:  # tpyc: ok
        xs.append(3)
        return len(xs)

    def via_sibling(xs: list[int32]) -> int32:  # tpyc: ok
        return tally(xs)

    data = [1]
    print("sibling_after:", via_sibling(data), data)


def lambda_param_position() -> None:
    # a sub-scope binding does NOT make the name local to the nested body: a
    # lambda PARAM binds only inside the lambda, so the shadow gate stays on
    # and only reads outside the sub-scope reject
    # (nested_def/error_name_collision_lambda_param)
    def tally(xs: list[int32]) -> int32:  # tpyc: ok
        step: Callable[[int32], int32] = lambda tally: tally + 1
        xs.append(step(5))
        return len(xs)

    data = [1]
    print("lambda_param:", tally(data), data)


def comp_var_position() -> None:
    # comprehension variable: its own scope, same rule
    def tally(xs: list[int32]) -> int32:  # tpyc: ok
        doubled = [tally * 2 for tally in xs]
        xs.append(doubled[0])
        return len(xs)

    data = [3]
    print("comp_var:", tally(data), data)


def except_as_position() -> None:
    # `except ... as tally`: the handler's read is the caught exception
    def tally(xs: list[int32]) -> int32:  # tpyc: ok
        try:
            raise ValueError("boom")
        except ValueError as tally:
            print("except_as caught:", tally)
        xs.append(6)
        return len(xs)

    data = [1]
    # the call is hoisted out of the print: an argument that writes to stdout
    # interleaves ahead of the earlier arguments
    # (BUGS.md#subexpression-right-to-left-eval)
    n = tally(data)
    print("except_as:", n, data)


def match_capture_position() -> None:
    # match capture: the arm's read is the capture. A read of the name OUTSIDE
    # the arm is a located sema error here ('tally' is not callable), because
    # sema binds the capture for the whole body
    def tally(xs: list[int32]) -> int32:  # tpyc: ok
        match xs[0]:
            case tally:
                xs.append(tally)
        return len(xs)

    data = [4]
    print("match_capture:", tally(data), data)


def method_position() -> None:
    c = Counter()
    m = [5]
    print("method:", c.tally(m), m)


def main() -> None:
    # a body that does NOT rebind the name still reaches the module function
    print("module:", tally([1, 2, 3]))
    free_position()
    closure_position()
    own_param_position()
    own_local_position()
    sibling_after_position()
    lambda_param_position()
    comp_var_position()
    except_as_position()
    match_capture_position()
    method_position()


main()
