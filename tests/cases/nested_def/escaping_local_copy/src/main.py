# An escaping closure whose captured local is USED after it: the capture cannot
# move, so it copies and warns, and the later mutation is NOT visible through
# the closure (CPython captures by reference and sees it). The sections after
# the first are the spellings of "used after" that are not a plain statement:
# a sibling `def` written after the closure, one written before it and called
# after, one in an `if` arm, a lambda, a `nonlocal` rebind, a sibling reached
# only through another sibling, and one of two same-named defs in an
# `if`/`else`. The last two sections are the rule's deliberate
# over-approximations: a sibling that is never called, and a lambda parameter
# that shadows the local.
from typing import Callable
from tpy import int32

class Config:
    value: int32
    def __init__(self, v: int32) -> None:
        self.value = v

def make_getter() -> Callable[[], int32]:
    cfg = Config(42)
    def get_value() -> int32:  # tpyc: warning(/copies local 'cfg'.*used after closure/)
        return cfg.value
    cfg.value = 999
    print(cfg.value)
    return get_value

def make_sibling_reader() -> Callable[[], int32]:
    data = [1, 2]
    # a SIBLING def is the ONLY thing that reads the local after the escaping
    # one -- nothing at this level does -- and that read is still a use after
    # the closure, so the capture copies and the mutation stays invisible
    # through it. The sibling reports from inside its own body for the same
    # reason: a read out here would produce the warning by itself.
    def size() -> int32:  # tpyc: warning(/copies local 'data'.*used after closure/)
        return len(data)
    def grow() -> None:  # tpyc: ok
        data.append(3)
        print("outer:", len(data))
    grow()
    return size

def make_before_sibling() -> Callable[[], int32]:
    data = [4]
    # the sibling is written BEFORE the escaping def and only NAMED after it:
    # that call can still reach the local, so it is a later use too
    def grow() -> None:  # tpyc: ok
        data.append(5)
        print("before:", len(data))
    def size() -> int32:  # tpyc: warning(/copies local 'data'.*used after closure/)
        return len(data)
    grow()
    return size

def make_if_arm_sibling() -> Callable[[], int32]:
    data = [6]
    flag = True
    def size() -> int32:  # tpyc: warning(/copies local 'data'.*used after closure/)
        return len(data)
    # the sibling sits in an `if` body, so only the recursive walk finds it
    if flag:
        def grow() -> None:  # tpyc: ok
            data.append(7)
            print("if_arm:", len(data))
        grow()
    return size

def make_lambda_reader() -> Callable[[], int32]:
    data = [8]
    data.append(9)
    data.append(10)
    def size() -> int32:  # tpyc: warning(/copies local 'data'.*used after closure/)
        return len(data)
    # a lambda body is its own scope, but its read still reaches the local --
    # and it is the only read after the closure, so it is what produces the
    # warning. The lambda copies the local too, with no diagnostic of its own
    # (BUGS.md#callable-local-lambda-copies-capture-silently)
    read: Callable[[], int32] = lambda: len(data)
    print("lambda:", read())
    return size

def make_nonlocal_sibling() -> Callable[[], int32]:
    data = [11]
    # the rebind is also a REASSIGNMENT of the captured name, so the stale
    # value-capture warning fires beside the cost one
    def size() -> int32:  # tpyc: warning(/copies local 'data'.*used after closure/) warning(/'data' is reassigned after the closure is created/)
        return len(data)
    # `nonlocal` names the enclosing binding instead of shadowing it, so the
    # rebind under it writes to the very local the closure captured
    def replace() -> None:  # tpyc: ok
        nonlocal data
        data = [12, 13, 14]
        print("nonlocal:", len(data))
    replace()
    return size

def make_transitive_sibling() -> Callable[[], int32]:
    data = [15]
    # `reader` is written before the closure and named by nobody after it --
    # only `caller` is, and it reaches `reader`, so the chain still counts
    def reader() -> None:  # tpyc: ok
        data.append(16)
        print("transitive:", len(data))
    def size() -> int32:  # tpyc: warning(/copies local 'data'.*used after closure/)
        return len(data)
    def caller() -> None:  # tpyc: ok
        reader()
    caller()
    return size

def make_two_arms_same_name() -> Callable[[], int32]:
    data = [17]
    flag = False
    def size() -> int32:  # tpyc: warning(/copies local 'data'.*used after closure/)
        return len(data)
    # two defs called `h`, one per arm: only the second reads the local, so
    # keying the sibling lookup by NAME would lose it
    if flag:
        def h() -> None:  # tpyc: ok
            print("arms: noop")
        h()
    else:
        def h() -> None:  # tpyc: ok
            data.append(18)
            print("arms:", len(data))
        h()
    return size

def make_named_not_called() -> Callable[[], int32]:
    data = [19]
    data.append(20)
    print("named_not_called:", len(data))
    def size() -> int32:  # tpyc: warning(/copies local 'data'.*used after closure/)
        return len(data)
    # nothing after the closure reads the local except this sibling, which is
    # never called -- a deliberate over-approximation, since proving it is
    # unreachable is not this pass's job
    def never_called() -> int32:  # tpyc: ok
        return len(data)
    return size

def make_lambda_param_shadow() -> Callable[[], int32]:
    data = [21]
    data.append(22)
    def size() -> int32:  # tpyc: warning(/copies local 'data'.*used after closure/)
        return len(data)
    # the lambda's PARAMETER shadows the local, so its body cannot reach it --
    # the other deliberate over-approximation: the scan reads lambda bodies
    # without tracking what their parameters bind
    step: Callable[[int32], int32] = lambda data: data + 1
    print("lambda_param_shadow:", step(1))
    return size

def main() -> None:
    getter = make_getter()
    print(getter())
    # hoisted out of the print: the factory writes to stdout, and an argument
    # that does interleaves ahead of the earlier arguments
    # (BUGS.md#subexpression-right-to-left-eval)
    seen = make_sibling_reader()()
    print("sibling:", seen)
    before = make_before_sibling()()
    print("before_sibling:", before)
    arm = make_if_arm_sibling()()
    print("if_arm_sibling:", arm)
    lam = make_lambda_reader()()
    print("lambda_sibling:", lam)
    nl = make_nonlocal_sibling()()
    print("nonlocal_sibling:", nl)
    chain = make_transitive_sibling()()
    print("transitive_sibling:", chain)
    arms = make_two_arms_same_name()()
    print("arms_sibling:", arms)
    uncalled = make_named_not_called()()
    print("named_not_called_sibling:", uncalled)
    shadow = make_lambda_param_shadow()()
    print("lambda_param_sibling:", shadow)

main()
