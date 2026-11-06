# Two loops (or a loop and an arm) binding one name to different literals
# or storages: the one declaration keeps the first binding and joins them.
from tpy import int32, int64
from fixtures import Pic


# --- Two loops binding ONE name to a container LITERAL. Two literals are
# distinct pending types even when they denote the same type, so the shared
# entry has to keep the FIRST binding's -- re-recording the later one moves
# the declaration past the first loop and declares the name twice. The alias
# taken in the first body shows which list the read reached.

# Flat sibling loops; the second runs zero times on the first call.
def two_loop_list_sibling(n: int32) -> None:
    for j in range(2):
        xs = [j]  # tpyc: ok
        held = xs
    for i in range(n):
        xs = [i, i]
    xs.append(9)
    print("two_loop_list_sibling", xs, held)


# ... the same two-loop shape over str/bytes, where the ONE declaration also
# has to agree on the STORAGE: a view slot holding the second loop's owned
# concatenation would dangle past it, so the join takes the strictest binding
# and the slot owns whenever any binding does. `two_loop_str_views` renders
# `std::string t` -- an owning slot copying each slice -- although BOTH its
# bindings slice the live parameter: a slice is one of the shapes the rebind
# check fails closed on, BUGS.md#view-storage-join-stricter-after-first-binding.
# The values are long enough that a dangling read would not survive in an SSO
# buffer.
def two_loop_str_views(s: str, n: int32) -> None:
    for j in range(2):
        t = s[j:]  # tpyc: ok
    for i in range(n):
        t = s[i + 1:]
    print("two_loop_str_views", t)


def two_loop_str_owned(s: str, n: int32) -> None:
    for j in range(2):
        t = "a literal long enough to outrun any small-string buffer"  # tpyc: ok
    for i in range(n):
        t = s + str(i)
    print("two_loop_str_owned", t)


def two_loop_bytes_owned(b: bytes, n: int32) -> None:
    for j in range(2):
        v = b"a literal long enough to outrun any small buffer"  # tpyc: ok
    for i in range(n):
        v = b + b"0123456789012345678901234567890123456789"
    print("two_loop_bytes_owned", v)


# ... and the MIXED pair: the first loop's binding is a slice (a view of the
# live parameter), the second's is a concatenation that owns. One local, one
# storage -- the join owns, so the slice binding copies into it rather than
# leaving a view over the concatenation's dead temporary.
def two_loop_str_view_then_owned(s: str, n: int32) -> None:
    for i in range(2):
        t = s[i:]  # tpyc: ok
    for j in range(n):
        t = s + str(j)
    print("two_loop_str_view_then_owned", t)


def two_loop_bytes_view_then_owned(b: bytes, n: int32) -> None:
    for i in range(2):
        v = b[i:]  # tpyc: ok
    for j in range(n):
        v = b + b"0123456789012345678901234567890123456789"
    print("two_loop_bytes_view_then_owned", v)


# ... and the same join with no loop, at the two faces a rebind into an
# already-owning bytes slot can take: a slice EXPRESSION and a view LOCAL.
# Both need the family's view->owned construction -- `::tpy::Bytes` has no
# `operator=` from a span, so a bare assign would not compile.
def flat_bytes_owned_from_view(b: bytes) -> None:
    t = b + b"0123456789012345678901234567890123456789"
    t = b[0:2]  # tpyc: ok
    v = b + b"0123456789012345678901234567890123456789"
    w = b[1:]
    v = w  # tpyc: ok
    print("flat_bytes_owned_from_view", t, v)


# ... the int-literal join: the second loop's value does not fit the first
# binding's default width, so the one local is the wider type.
def two_loop_int_widths(n: int32) -> None:
    for i in range(2):
        x = 1  # tpyc: ok
    for j in range(n):
        # tpyc: warning(/outside default int32 range/)
        x = 1099511627776
    print("two_loop_int_widths", x)


# ... the same over a literal and a spelled width: the literal-seeded local
# is the type its stores give.
def two_loop_int_widen(n: int32) -> None:
    for i in range(2):
        x = 1  # tpyc: type(int64)
    for j in range(n):
        x = int64(1099511627776)
    print("two_loop_int_widen", x)


# ... an EMPTY list literal in the first loop: the element type comes from the
# binding that has one. The alias taken in the second body shows the append
# after the loops lands in the one list, not in a copy.
def two_loop_empty_list(n: int32) -> None:
    for i in range(2):
        xs = []  # tpyc: ok
    # Narrowed: `held` is bound only by the second loop, so that loop has to
    # provably run for the read after it.
    if n < 1:
        return
    for j in range(n):
        xs = [1, 2]
        held = xs
    xs.append(9)
    print("two_loop_empty_list", xs, held)


# ... the same join with no loop at all: position does not change the answer.
def flat_empty_list() -> None:
    xs = []  # tpyc: ok
    xs = [1, 2]
    xs.append(9)
    print("flat_empty_list", xs)


# ... and with the empty binding in an `if` arm, the second at function level.
def arm_empty_list(flag: bool) -> None:
    if flag:
        xs = []  # tpyc: ok
    xs = [1, 2]
    xs.append(9)
    print("arm_empty_list", xs)


# ... `None` then a record: the join is the Optional. The alias taken in the
# body is mutated after the loops and read through the other name.
def two_loop_none_then_record(n: int32) -> None:
    for i in range(2):
        p = None  # tpyc: ok
    # Narrowed: `saved` is bound only by the second loop, so that loop has to
    # provably run for the read after it.
    if n < 1:
        print("two_loop_none_then_record none")
        return
    for j in range(n):
        p = Pic(j)
        saved = p
    if p is not None:
        saved.n += 100
        print("two_loop_none_then_record", p.n, saved.n)
    else:
        print("two_loop_none_then_record none")


# ... the second loop nested in an `if` arm, over a dict.
def two_loop_dict_arm(flag: bool) -> None:
    for j in range(2):
        d = {j: j}  # tpyc: ok
        held = d
    if flag:
        for i in range(2):
            d = {i: i * 10}
    d[7] = 7
    print("two_loop_dict_arm", sorted(d.items()), sorted(held.items()))


# The binding loop BEFORE an `if` (part_blocks' blk_pre_loop_arm shape), with
# the arm rebinding the name from an owning source: the one local owns, so the
# loop's slice binding copies.
def blk_loop_then_arm(s: str, flag: bool) -> None:
    for i in range(2):
        t = s[i:]  # tpyc: ok
    if flag:
        t = s + "tail"
    print("blk_loop_then_arm", t)


def main() -> None:
    two_loop_list_sibling(0)
    two_loop_list_sibling(2)
    two_loop_dict_arm(False)
    two_loop_dict_arm(True)
    long_s = "a parameter long enough that a dangling view would show, truly"
    two_loop_str_views(long_s, 0)
    two_loop_str_views(long_s, 2)
    two_loop_str_owned(long_s, 0)
    two_loop_str_owned(long_s, 2)
    long_b = b"a parameter long enough that a dangling view would show"
    two_loop_bytes_owned(long_b, 0)
    two_loop_bytes_owned(long_b, 2)
    two_loop_str_view_then_owned(long_s, 0)
    two_loop_str_view_then_owned(long_s, 2)
    two_loop_bytes_view_then_owned(long_b, 0)
    two_loop_bytes_view_then_owned(long_b, 2)
    flat_bytes_owned_from_view(long_b)
    two_loop_int_widths(0)
    two_loop_int_widths(2)
    two_loop_int_widen(0)
    two_loop_int_widen(2)
    two_loop_empty_list(2)
    flat_empty_list()
    arm_empty_list(True)
    arm_empty_list(False)
    two_loop_none_then_record(0)
    two_loop_none_then_record(2)
    blk_loop_then_arm(long_s, True)
    blk_loop_then_arm(long_s, False)
