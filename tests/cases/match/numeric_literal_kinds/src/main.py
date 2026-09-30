# A numeric literal pattern compares with `==`, which is numeric ACROSS
# bool/int/float, so the literal's kind need not equal the subject's/field's
# type: `case 1:` matches a 1.0 subject. The reverse spelling is warned and
# FOLDED: a float literal against a subject that holds whole numbers only
# (fixed int, `bool`, `int`) is almost certainly a typo for the int literal,
# so sema rewrites `case 2.0:` to `case 2:` and says so; a non-integral value
# there can never match and is an error (error_literal_float_never_matches),
# as does a value the subject type cannot hold at all
# (error_literal_int_out_of_range). A folded label is an ordinary switch
# label, so the last two sections pin the tier holes that reaches: an arm
# whose labels OVERLAP another arm's demotes to the `==` chain.
# One section per position the match can sit in; each prints its own name.
# `case True:` is NOT in this family -- PEP 634 compares True/False by
# identity, so it only matches a bool (pinned by error_literal_bool_identity).
from typing import Iterator
from tpy import int32, float32, error_return, ReturnException


class P:
    def __init__(self, f: float, i: int32, b: bool) -> None:
        self.f = f
        self.i = i
        self.b = b


class Missing(Exception, ReturnException):
    pass


# free function -- int literal against a float subject
def int_lit_at_float(v: float) -> str:
    match v:
        case 1:  # tpyc: ok
            return "hit"
        case _:
            return "miss"


# free function -- the folded float label is an ordinary switch label, so the
# fixed-int subject keeps the primitive switch
def float_lit_at_int(v: int32) -> str:
    match v:
        case 2.0:  # tpyc: warning(/float literal pattern 2\.0 against subject type 'int32'; spell it 2/)
            return "hit"
        case _:
            return "miss"


# free function -- int and float literals against a bool subject
def lits_at_bool(v: bool) -> str:
    match v:
        case 1:  # tpyc: ok
            return "one"
        case 0:  # tpyc: ok
            return "zero"
        case _:
            return "miss"


def float_lit_at_bool(v: bool) -> str:
    match v:
        case 1.0:  # tpyc: warning(/spell it 1/)
            return "hit"
        case _:
            return "miss"


# free function -- a BigInt (`int`) subject, which has no comparison against a
# double at all; the fold is what gives the arm a render
def float_lit_at_bigint(v: int) -> str:
    match v:
        case 2.0:  # tpyc: warning(/spell it 2/)
            return "hit"
        case _:
            return "miss"


# free function -- int literal against a float32 subject
def int_lit_at_f32(v: float32) -> str:
    match v:
        case 1:  # tpyc: ok
            return "hit"
        case _:
            return "miss"


# or-pattern -- the fold reaches an alternative, so the whole arm still spells
# switch labels
def or_alt(v: int32) -> str:
    match v:
        case 1 | 2.0:  # tpyc: warning(/spell it 2/)
            return "hit"
        case _:
            return "miss"


# `as` binding over a folded literal -- the fold reaches the inner pattern
def as_alt(v: int32) -> str:
    match v:
        case 2.0 as x:  # tpyc: warning(/spell it 2/)
            return "hit-" + str(x)
        case _:
            return "miss"


# guarded arm -- the folded label is a plain switch label, so the guard nests
# in its case block instead of demoting the subject onto the `==` chain
def guarded_label(v: int32, flag: bool) -> str:
    match v:
        case 2.0 if flag:  # tpyc: warning(/spell it 2/)
            return "guarded"
        case 2:  # tpyc: ok
            return "plain"
        case _:
            return "miss"


# guarded arm whose only sibling is the catch-all: when the guard fails the
# switch leaves its own case block for `default:`
def guard_default(v: int32, flag: bool) -> str:
    match v:
        case 2.0 if flag:  # tpyc: warning(/spell it 2/)
            return "guarded"
        case _:
            return "fallthrough"


# or-pattern OVERLAP across arms -- `2` labels two arms whose label sets
# differ; a switch could spell it only by duplicating each shared body under
# every label it covers, so the tier demotes to the body-once `==`
# chain and the second arm stays live when the guard fails
def or_overlap_int(v: int32, flag: bool) -> str:
    match v:
        case 1 | 2 if flag:  # tpyc: ok
            return "or-guard"
        case 2:  # tpyc: ok
            return "two"
        case _:
            return "other"


# the same overlap reached THROUGH the fold: `2.0` becomes the very label the
# arm below already carries
def or_overlap_folded(v: int32, flag: bool) -> str:
    match v:
        case 1 | 2.0 if flag:  # tpyc: warning(/spell it 2/)
            return "or-guard"
        case 2:  # tpyc: ok
            return "two"
        case _:
            return "other"


# method -- field patterns, one per numeric field type
class Probe:
    def __init__(self, tag: str) -> None:
        self.tag = tag

    def describe(self, p: P) -> str:
        match p:
            case P(f=1):  # tpyc: ok
                return self.tag + "-f"
            case P(i=1.0):  # tpyc: warning(/float literal pattern 1\.0 against field 'i' of type 'int32'; spell it 1/)
                return self.tag + "-i"
            case P(b=1):  # tpyc: ok
                return self.tag + "-b"
            case _:
                return self.tag + "-none"


# generator body -- the match sits in a resumable frame
def gen(vals: list[float]) -> Iterator[str]:
    for v in vals:
        match v:
            case 1:  # tpyc: ok
                yield "one"
            case _:
                yield "other"


class Scope:
    def __enter__(self) -> "Scope":
        return self

    def __exit__(self, exc_type, exc_value, tb) -> bool:
        return False


# context-manager body and try/finally
def in_with(v: int32) -> str:
    out = "?"
    with Scope():
        try:
            match v:
                case 1:  # tpyc: ok
                    out = "hit"
                case _:
                    out = "miss"
        finally:
            out = out + "!"
    return out


# @error_return body -- an int32 result.
@error_return(Missing)
def checked(v: float) -> int32:
    match v:
        case 1:  # tpyc: ok
            return 7
        case _:
            raise Missing


# match arm -- a nested match inside a case body
def nested(outer: int32, inner: float) -> str:
    match outer:
        case 1:  # tpyc: ok
            match inner:
                case 2:  # tpyc: ok
                    return "1/2"
                case _:
                    return "1/?"
        case _:
            return "?"


# Optional subject -- the literal is compared against the INNER type, so the
# fold has to reach the inner dispatch too
def opt_lit(v: int32 | None) -> str:
    match v:
        case None:
            return "none"
        case 1.0:  # tpyc: warning(/spell it 1/)
            return "hit"
        case _:
            return "miss"


# closure -- a nested def carries the same arm
def closure_call(v: float) -> str:
    def inner() -> str:
        match v:
            case 1:  # tpyc: ok
                return "hit"
            case _:
                return "miss"
    return inner()


# module-level statement -- top-level codegen uses global slots
G: float = 1.0
GRES = "?"
match G:
    case 1:  # tpyc: ok
        GRES = "hit"
    case _:
        GRES = "miss"


def main() -> None:
    print("free-int-at-float", int_lit_at_float(1.0), int_lit_at_float(2.0))
    print("free-float-at-int", float_lit_at_int(2), float_lit_at_int(1))
    print("free-int-at-bool", lits_at_bool(True), lits_at_bool(False))
    print("free-float-at-bool", float_lit_at_bool(True), float_lit_at_bool(False))
    print("free-float-at-bigint", float_lit_at_bigint(2), float_lit_at_bigint(1))
    print("free-int-at-f32", int_lit_at_f32(1.0), int_lit_at_f32(2.0))

    print("or-alt", or_alt(1), or_alt(2), or_alt(3))
    print("as-alt", as_alt(2), as_alt(1))
    print("guarded", guarded_label(2, True), guarded_label(2, False),
          guarded_label(1, True))
    print("guard-default", guard_default(2, True), guard_default(2, False),
          guard_default(1, True))
    print("or-overlap-int", or_overlap_int(1, True), or_overlap_int(1, False),
          or_overlap_int(2, True), or_overlap_int(2, False),
          or_overlap_int(3, False))
    print("or-overlap-folded", or_overlap_folded(1, True),
          or_overlap_folded(1, False), or_overlap_folded(2, True),
          or_overlap_folded(2, False), or_overlap_folded(3, False))

    pr = Probe("m")
    p = P(1.0, 0, False)
    print("method-field", pr.describe(p), pr.describe(P(0.0, 1, False)),
          pr.describe(P(0.0, 0, True)), pr.describe(P(0.0, 0, False)))
    # The subject is borrowed, not copied: a write after the first match is
    # what the next match reads.
    p.f = 9.0
    print("method-mutate", pr.describe(p))

    print("generator", list(gen([1.0, 2.0])))
    print("with-finally", in_with(1), in_with(2))

    # The results are bound before printing: an argument that raises inside a
    # print call still emits the earlier arguments (BUGS.md#print-arg-output-interleaves).
    try:
        er1 = checked(1.0)
    except Missing:
        er1 = -1
    try:
        er2 = checked(2.0)
    except Missing:
        er2 = -1
    print("error-return", er1, er2)

    print("match-arm", nested(1, 2.0), nested(1, 3.0), nested(2, 2.0))
    print("optional", opt_lit(None), opt_lit(1), opt_lit(2))
    print("closure", closure_call(1.0), closure_call(2.0))
    print("module-level", GRES)


main()
