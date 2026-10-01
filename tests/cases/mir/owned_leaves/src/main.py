# The MIR verdict matrix: owned leaves (BigInt, str, String, bytes) at every position, the refused
# shapes, and the record shapes that reach the certified and conflict rungs.
from typing import Iterator

from tpy import int32, char, String, Own, StrView

G: str = "glob"
N: int = 7
B: bytes = b"gb"
hits: int32 = 0


class Rec:
    name: str
    count: int32

    # constructor: owned-leaf record fields are not lowered yet
    def __init__(self, name: str, count: int32):  # tpyc: mir(uncovered /^unsupported record fields$/)
        self.name = name
        self.count = count

    # method: an int32 field read and a borrowed str parameter
    def positive_x(self, s: str) -> bool:  # tpyc: mir(covered)
        return self.count > 0 and s == "x"


class Point:
    x: int32
    y: int32

    # constructor: scalar record fields lower
    def __init__(self, x: int32, y: int32):  # tpyc: mir(covered)
        self.x = x
        self.y = y


# free function: borrowing parameters, a view, two const refs and a bytes view, read in place
def param_borrow(s: str, n: int, t: String, b: bytes) -> bool:  # tpyc: mir(covered) mir_summary(known)
    return s == "x" and n > 0 and t != t and b == b


# free function: a by-value parameter is the body's storage and returns itself
def by_value(s: Own[str]) -> str:  # tpyc: mir(covered) mir_summary(known)
    return s


# free function: a local copies the const-ref parameter, then is replaced in place
def local(n: int) -> int:  # tpyc: mir(covered) mir_summary(known)
    total = n
    total = total * 2
    return total


# free function: `a * b` is a temporary its comparison's full expression ends
def temporary(a: int, b: int) -> bool:  # tpyc: mir(covered) mir_summary(known)
    return a * b > a


# free function: the view parameter is copied into the result
def ret_copy(s: str) -> str:  # tpyc: mir(covered) mir_summary(known)
    return s


# free function: owned-leaf globals read through readonly external handles
def global_read() -> int:  # tpyc: mir(covered) mir_summary(known)
    x = N
    return x + N


def global_print() -> None:  # tpyc: mir(covered) mir_summary(opaque /^summary output effect$/)
    print(G, N, B)


# free function: print reads owned leaves through holders
def prints(s: str, n: int, b: bytes) -> None:  # tpyc: mir(covered) mir_summary(opaque /^summary output effect$/)
    print(s, n, b, "lit")


# free function: element reads are raising operations
def element(s: str, b: bytes, i: int32) -> bool:  # tpyc: mir(covered) mir_summary(known)
    return s[i] == 'a' and b[i] == 1


# free function: the reassigned view parameter gets an owned copy; `s = s + "x"` appends to it.
# The return copies the owned local once more (BUGS.md#owned-local-str-return-copies).
def param_copy(s: str) -> str:  # tpyc: mir(covered) mir_summary(known)
    s = s + "x"
    return s


# free function: `s: str = a` copies the borrowed read, then appends in place
def append(a: str) -> str:  # tpyc: mir(covered) mir_summary(known)
    s: str = a
    s += "y"
    s += a
    return s


# free function: int32 -> BigInt and char -> str conversions
def convert(c: char, i: int32) -> str:  # tpyc: mir(covered) mir_summary(known)
    x: int = i
    s: str = c
    return s


# free function: a literal return materializes an owned constant
def literal() -> str:  # tpyc: mir(covered) mir_summary(known)
    return "abc"


# free function: `int32.__int__` promotes `i` into BigInt's `+`: a conversion feeding the operator
def promoted(total: int, i: int32) -> int:  # tpyc: mir(covered) mir_summary(known)
    return total + i


def promoted_compare(i: int32, total: int) -> bool:  # tpyc: mir(covered) mir_summary(known)
    return i == total


# free function: a BigInt copy's allocation failure is a panic: no exceptional exit
def big_copy(n: int) -> int:  # tpyc: mir(covered) mir_summary(known)
    return n


# free function: loop temporaries initialize per activation
def loop(n: int) -> int:  # tpyc: mir(covered) mir_summary(known)
    k = n
    while k < n * 2:
        k = k + 1
    return k


# free function: mixed float/BigInt and int32/BigInt operations, negation
def float_big(x: float, n: int) -> float:  # tpyc: mir(covered) mir_summary(known)
    return x + n


def mixed(n: int, i: int32) -> bool:  # tpyc: mir(covered) mir_summary(known)
    return i > n


def neg(n: int) -> int:  # tpyc: mir(covered) mir_summary(known)
    return -n


# callees for the argument and result sections
def takes(s: str) -> int32:  # tpyc: mir(covered) mir_summary(known)
    return 1


def takes_big(n: int) -> int32:  # tpyc: mir(covered) mir_summary(known)
    return 1


def takes_own(s: Own[str]) -> int32:  # tpyc: mir(covered) mir_summary(known)
    return 1


def scaled(x: float, n: int) -> int32:  # tpyc: mir(covered) mir_summary(known)
    return 1


# free function: lent for the call, a borrow of the view parameter's storage
def calls_with_str(s: str) -> int32:  # tpyc: mir(covered) mir_summary(known)
    return takes(s)


def lends(s: str, n: int) -> int32:  # tpyc: mir(covered) mir_summary(known)
    return takes(s) + takes_big(n)


# free function: a by-value parameter gets its own copy, built in the full expression's storage
def copies(s: str) -> int32:  # tpyc: mir(covered) mir_summary(known)
    return takes_own(s)


# free function: a borrowed literal is static storage; an owning one is materialized
def literal_arguments() -> int32:  # tpyc: mir(covered) mir_summary(known)
    return takes("lit") + takes_own("own")


def own_result(s: str) -> Own[str]:  # tpyc: mir(covered) mir_summary(known)
    return s


# free function: an `Own[str]` return is a plain owned str at the caller
def owned_result_caller(s: str) -> bool:  # tpyc: mir(covered) mir_summary(known)
    return own_result(s) == "x"


# free function: the callee returns by value, a fresh temporary, not a borrow of `s`
def fresh_result(s: str) -> bool:  # tpyc: mir(covered) mir_summary(known)
    return ret_copy(s) == "x"


def temporary_argument(s: str) -> int32:  # tpyc: mir(covered) mir_summary(known)
    return takes(ret_copy(s))


def result_local(s: str) -> str:  # tpyc: mir(covered) mir_summary(known)
    t = ret_copy(s)
    return t


# free function: owned-leaf globals as call arguments
def global_arguments() -> int32:  # tpyc: mir(covered) mir_summary(known)
    return takes(G) + takes_big(N)


# free function: `s` may borrow G itself; two readonly borrows of one storage never conflict
def overlap(s: str) -> bool:  # tpyc: mir(covered) mir_summary(known)
    return s == G and takes(G) == 1


# free function: writing a scalar global keeps the shape lowered
def overlap_write(s: str) -> bool:  # tpyc: mir(covered) mir_summary(opaque /^summary global access$/)
    global hits
    hits = 1
    return s == G


# free function: the storage-form literal is a temporary of the print call's full expression
def bytes_print() -> None:  # tpyc: mir(covered) mir_summary(opaque /^summary output effect$/)
    print(b"xy")


# free function: admitted expressions are arguments, each evaluated into a temporary before the call
def expression_arguments(x: float, n: int) -> int32:  # tpyc: mir(covered) mir_summary(known)
    return scaled(x * 1000, n + 1)


# free function: the call's owned result is a temporary of the print call's full expression
def print_call(s: str) -> None:  # tpyc: mir(covered) mir_summary(opaque /^summary output effect$/)
    print(ret_copy(s), end="")


# free function: an owned bytes literal return
def bytes_literal() -> bytes:  # tpyc: mir(covered) mir_summary(known)
    return b"ab"


# free function: a record parameter read through its field
def take_point(p: Point) -> int32:  # tpyc: mir(covered) mir_summary(known)
    return p.x


# free function: the record rvalue lives in a temporary the call borrows, and the certificate binds it
def rvalue_temp() -> int32:  # tpyc: mir(certified)
    return take_point(Point(7, 0))


# free function: `saved` aliases the loop's hoisted `p`, whose storage the next iteration's rebind replaces
def conditional_hoist() -> None:  # tpyc: mir(conflict /^replacement$/)
    saved = Point(0, 0)
    for i in range(5):
        p = Point(i, i * 3)
        if i > 2:
            saved = p  # tpyc: warning(/will not keep the object it was given/)
    print("conditional_hoist:", saved.x, saved.y)


# refused: a view local
def view_local(s: str) -> int32:  # tpyc: mir(uncovered /^view local$/)
    v = s[1:]
    return 1


# refused: an owned-leaf record field read
def str_field(r: Rec) -> bool:  # tpyc: mir(uncovered /^owned-leaf record field$/)
    return r.name == "x"


# refused: an owned-leaf record field write
def str_field_write(r: Rec, s: str) -> None:  # tpyc: mir(uncovered /^owned-leaf record field$/)
    r.name = s


# refused: an owned-leaf global write
def global_write() -> None:  # tpyc: mir(uncovered /^owned-leaf global write$/)
    global G
    G = "new"


# refused: a view return
def view_return(s: str) -> StrView:  # tpyc: mir(uncovered /^view return$/)
    return s


# refused: a bytearray parameter
def buffer(b: bytearray) -> int32:  # tpyc: mir(uncovered /^unsupported parameter type$/)
    return 1


# refused: THIR names the owning copy of a global as an argument temporary, which MIR does not admit yet
def global_copy() -> int32:  # tpyc: mir(uncovered /^unsupported owned-leaf expression$/)
    return takes_own(G)


# refused: no MIR place holds a view, so the conversion handing the view result to `takes` is refused
def view_caller(s: str) -> int32:  # tpyc: mir(uncovered /^unsupported coercion$/)
    return takes(view_return(s))


# refused: an f-string
def fstring(n: int) -> str:  # tpyc: mir(uncovered /^unsupported owned-leaf expression$/)
    return f"{n}"


# refused: a concatenation result compared in place
def concat_mixed(s: str, t: str) -> bool:  # tpyc: mir(uncovered /^uncertified binary operation$/)
    return s + t == "x"


# refused: the declaration names storage no initialization writes
def no_init(flag: bool) -> int:  # tpyc: mir(uncovered /^owned-leaf declaration needs an initializer$/)
    n: int
    if flag:
        n = 1
    else:
        n = 2
    return n


# refused: the concatenation's String is rendered in place as the str local
def string_as_str(s: str) -> str:  # tpyc: mir(uncovered /^conversion aliases its source$/)
    t = s + "x"
    return t


# generator: a resumable body is not lowered
def countdown(n: int) -> Iterator[int]:  # tpyc: mir(uncovered /^resumable body$/)
    k = n
    while k > 0:
        yield k
        k = k - 1


def main() -> None:
    print("param_borrow:", param_borrow("x", 1, String("t"), b"b"))
    print("by_value:", by_value("s"))
    print("local:", local(3))
    print("temporary:", temporary(2, 3))
    print("ret_copy:", ret_copy("r"))
    print("global_read:", global_read())
    print("global_print:", end=" ")
    global_print()
    print("prints:", end=" ")
    prints("s", 1, b"b")
    print("element:", element("abc", b"abc", 1))
    print("param_copy:", param_copy("p"))
    print("append:", append("a"))
    print("convert:", convert('c', 3))
    print("literal:", literal())
    print("promoted:", promoted(10, 3), promoted_compare(3, 3))
    print("big_copy:", big_copy(8))
    print("loop:", loop(4))
    print("float_big:", float_big(1.5, 2))
    print("mixed:", mixed(3, 4))
    print("neg:", neg(5))
    print("calls_with_str:", calls_with_str("c"))
    print("lends:", lends("l", 2))
    print("copies:", copies("c"))
    print("literal_arguments:", literal_arguments())
    print("owned_result_caller:", owned_result_caller("x"))
    print("fresh_result:", fresh_result("y"))
    print("temporary_argument:", temporary_argument("t"))
    print("result_local:", result_local("rl"))
    print("global_arguments:", global_arguments())
    print("overlap:", overlap("glob"))
    print("overlap_write:", overlap_write("glob"), hits)
    print("bytes_print:", end=" ")
    bytes_print()
    print("expression_arguments:", expression_arguments(0.5, 2))
    print("print_call:", end=" ")
    print_call("pc")
    print()
    print("bytes_literal:", bytes_literal())
    print("rvalue_temp:", rvalue_temp())
    conditional_hoist()
    print("view_local:", view_local("vl"))
    r = Rec("x", 2)
    print("str_field:", str_field(r))
    str_field_write(r, "w")
    print("str_field_write:", r.name)
    print("method:", r.positive_x("x"), r.positive_x("y"))
    print("view_return:", view_return("vr"))
    print("buffer:", buffer(bytearray(b"ba")))
    print("global_copy:", global_copy())
    print("view_caller:", view_caller("vc"))
    print("fstring:", fstring(42))
    print("concat_mixed:", concat_mixed("", "x"))
    print("no_init:", no_init(True), no_init(False))
    print("string_as_str:", string_as_str("s"))
    global_write()
    print("global_write:", G)
    for k in countdown(3):
        print("generator:", k)


main()
