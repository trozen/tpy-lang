# Module statements are one body (__tpy_init): an unannotated module
# variable's type is the join of its bindings, every binding form alike, as
# a function local's is; functions read the settled type.
from typing import Iterator
from tpy import Own, uint8, int32, int64


def show_assign() -> None:
    print("reader:", a)


# assign: a wider value joins the variable's type
a = uint8(255)  # tpyc: type(int32)
a = a + 1
print("assign:", a)
show_assign()

# aug: an augmented result widens it too
b = uint8(200)  # tpyc: type(int32)
b += 100
print("aug:", b)

# for: a loop target joins the bindings of its body
D = [uint8(255)]
for g in D:  # tpyc: type(int32)
    g += 1
    print("for:", g)

# unpack_literal: a literal tuple unpack rebinds each target
u1 = uint8(1)
u2 = uint8(2)
u1, u2 = int64(5000000000), 3  # tpyc: type(int64)
print("unpack_literal:", u1, u2)

# unpack_value: a tuple value's elements join the targets they bind
p = (int64(5000000000), uint8(7))
v1 = uint8(0)  # tpyc: type(int64)
v1, v2 = p
print("unpack_value:", v1, v2)

# walrus: a walrus rebind joins the variable
w = uint8(1)  # tpyc: type(int32)
print("walrus:", (w := w + 300))
print("walrus:", w)

# walrus_comprehension: a comprehension's walrus binds the module variable
c = uint8(1)  # tpyc: type(int64)
cs = [(c := int64(5000000000)) for i in range(1)]
print("walrus_comprehension:", cs, c)


# walrus_comprehension_fn: in a function body too, a comprehension's walrus
# joins the local it binds
def walrus_comprehension_fn() -> None:
    x = uint8(255)  # tpyc: type(int32)
    xs = [(x := x + 1) for i in range(1)]
    print("walrus_comprehension_fn:", x, xs)


walrus_comprehension_fn()

# match: a capture is one more binding
m = uint8(1)  # tpyc: type(int64)
match int64(5000000000):
    case m:
        pass
print("match:", m)

# list: element cells join a later wider append
xs = [(1, 2)]  # tpyc: type(list[tuple[int64, int32]])
xs.append((int64(5000000000), 3))
print("list:", xs)

# list_empty: an empty list's cells are born at its first store
ys = []  # tpyc: type(list[int64])
ys.append(1)
ys.append(int64(5000000000))
print("list_empty:", ys)

# dict: key and value cells
d = {1: 2}  # tpyc: type(dict[int32, int64])
d[3] = int64(5000000000)
print("dict:", d)

# dict_empty
e = {}  # tpyc: type(dict[int32, int64])
e[1] = 1
e[2] = int64(5000000000)
print("dict_empty:", e)

# set
s = {1}  # tpyc: type(set[int64])
s.add(int64(5000000000))
print("set:", sorted(s))

# set_empty
t = set()  # tpyc: type(set[int64])
t.add(1)
t.add(int64(5000000000))
print("set_empty:", sorted(t))

# typed_widen: a global first bound to a typed integer joins a wider store
w8 = uint8(255)  # tpyc: type(int32)
for i in range(2):
    w8 = w8 + 1
print("typed_widen:", w8)

# literal_widen: an int literal no first type holds joins too
x = int32(1)  # tpyc: type(int)
for i in range(2):
    x = 5000000000  # tpyc: warning(/outside default int32 range/)
print("literal_widen:", x)

# aug_then_int: an augmented store and a later int store join the literal seed
ai = 0  # tpyc: type(int)
ai += 5
ai = int(20)
print("aug_then_int:", ai)

# walrus_first: a walrus first binding joins a later walrus store
print("walrus_first:", (wy := 0))
print("walrus_first:", (wy := int64(5000000000)))


def show_views() -> None:
    print("view_reader:", src, sv)


# view_rebind: a slice of a module string the module later rebinds owns its
# characters, and so does a slice of a loop element whose list grows
src = "x" * 5
sv = src[1:]
lst = ["ab" * 3]
for ch in lst:
    cv = ch[1:]
    # The function twin warns the same (BUGS.md#append-then-break-warns-iteration-mutation).
    lst.append("zz")  # tpyc: warning(/Mutation of 'lst' while iterating/)
    break
src = "y" * 2
print("view_rebind:", sv, cv)
show_views()

# walrus_comprehension_first: a comprehension walrus's first binding joins a
# later wider store
fs = [i for i in range(1) if (fw := uint8(1)) > 0]
fw = int64(5000000000)
print("walrus_comprehension_first:", fs, fw)


# walrus_comprehension_first_fn: the same in a function body
def walrus_comprehension_first_fn() -> None:
    gs = [i for i in range(1) if (gw := uint8(1)) > 0]
    gw = int64(5000000000)  # tpyc: type(int64)
    print("walrus_comprehension_first_fn:", gs, gw)


walrus_comprehension_first_fn()

# literal_seed: a variable first bound to a literal joins a wider binding of
# every form, as one first bound to a typed value does
lw = 0  # tpyc: type(int64)
print("literal_seed walrus:", (lw := int64(5000000000)))
lc = 0  # tpyc: type(int64)
lcs = [(lc := int64(5000000000)) for i in range(1)]
print("literal_seed walrus_comprehension:", lcs, lc)
lu = 0  # tpyc: type(int64)
lu, lu2 = p
print("literal_seed unpack:", lu, lu2)
lf = 0  # tpyc: type(int64)
for lf in [int64(5000000000)]:
    pass
print("literal_seed for:", lf)
lm = 0  # tpyc: type(int64)
match int64(5000000000):
    case lm:
        pass
print("literal_seed match:", lm)


# literal_seed_fn: the same in a function body
def literal_seed_fn(q: tuple[int64, uint8]) -> None:
    lw = 0  # tpyc: type(int64)
    print("literal_seed_fn walrus:", (lw := int64(5000000000)))
    lc = 0  # tpyc: type(int64)
    lcs = [(lc := int64(5000000000)) for i in range(1)]
    print("literal_seed_fn walrus_comprehension:", lcs, lc)
    lu = 0  # tpyc: type(int64)
    lu, lu2 = q
    print("literal_seed_fn unpack:", lu, lu2)
    lf = 0  # tpyc: type(int64)
    for lf in [int64(5000000000)]:
        pass
    print("literal_seed_fn for:", lf)
    lm = 0  # tpyc: type(int64)
    match int64(5000000000):
        case lm:
            pass
    print("literal_seed_fn match:", lm)


literal_seed_fn(p)

# block_def: a def inside a module block is callable inside that block
if True:
    def block_def() -> None:
        print("block_def:", 42)

    block_def()

# loop_target_view: a loop target read after its loop owns the element it
# last bound; the source changes after the loop
lsrc = ["x" * 30, "y" * 30]
for lt in lsrc:
    pass
lsrc[1] = "r" * 40  # the subject: the loop target must not see this write
lsrc.clear()
ldict = {"k" * 30: 1}
for lk in ldict:
    pass
ldict.clear()
lbytes = [b"z" * 30]
for lb in lbytes:
    pass
lbytes.clear()
lpairs = [("p" * 30, 1)]
for lpa, lpn in lpairs:
    pass
lpairs.clear()
print("loop_target_view:", lt, lk, lb, lpa, lpn)
if True:
    bsrc = ["q" * 30]
    for bt in bsrc:
        pass
    bsrc.clear()
    print("loop_target_view block:", bt)


def show_loop_rebind() -> None:
    print("loop_rebind_view reader:", rs, rb)


# loop_rebind_view: a str / bytes variable a loop rebinds keeps the last
# element after its source changes
rs = "initial"
rxs = ["a" * 30]
for rs in rxs:  # tpyc: ok
    pass
rxs[0] = "Z" * 30
rb = b"initial"
rbs = [b"c" * 30]
for rb in rbs:  # tpyc: ok
    pass
rbs.clear()
print("loop_rebind_view:", rs, rb)
show_loop_rebind()


# loop_rebind_view_fn: the same in a function body
def loop_rebind_view_fn() -> None:
    fs = "initial"
    fxs = ["d" * 30]
    for fs in fxs:  # tpyc: ok
        pass
    fxs[0] = "Z" * 30
    fb = b"initial"
    fbs = [b"e" * 30]
    for fb in fbs:  # tpyc: ok
        pass
    fbs.clear()
    print("loop_rebind_view_fn:", fs, fb)


loop_rebind_view_fn()


# loop_rebind_param: a loop rebinding a str parameter keeps the last element
# after its source changes
def loop_rebind_param(ps: str, pxs: list[str]) -> None:
    for ps in pxs:  # tpyc: ok
        pass
    pxs[0] = "Z" * 30
    print("loop_rebind_param:", ps)


loop_rebind_param("initial", ["f" * 30])


# loop_rebind_elem: so does one rebinding a local bound to a list element
def loop_rebind_elem(exs: list[str], eys: list[str]) -> None:
    ev = exs[0]
    for ev in eys:  # tpyc: ok
        pass
    eys[0] = "Z" * 30
    print("loop_rebind_elem:", ev)


loop_rebind_elem(["g" * 30], ["h" * 30])


# loop_rebind_gen: and one in a generator body, read after a suspension
def loop_rebind_gen(gxs: list[str]) -> Iterator[str]:
    gv = "initial"
    for gv in gxs:  # tpyc: ok
        pass
    yield gv
    gxs[0] = "Z" * 30
    yield gv


for gout in loop_rebind_gen(["i" * 30]):
    print("loop_rebind_gen:", gout)
# A loop rebinding a str local over an Iterator[str] source does not lower
# yet (BUGS.md#hoisted-loop-var-over-generator-rejected).

# loop_widen_after: a literal-seeded loop target joins its list's element,
# which a store after the loop widens; the next pass binds the wide value
mx = 7  # tpyc: type(int64)
my = [1, 2]
for mr in range(2):
    for mx in my:
        print("loop_widen_after:", mx)
    my.append(int64(5000000000))


# loop_widen_after_fn: the same in a function body
def loop_widen_after_fn() -> None:
    lx = 7  # tpyc: type(int64)
    ly = [1, 2]
    for r in range(2):
        for lx in ly:
            print("loop_widen_after_fn:", lx)
        ly.append(int64(5000000000))


loop_widen_after_fn()

# genexpr_walrus: a generator expression's walrus target that nothing else
# in its scope uses binds in the expression's own frame, unobservably
gtot = sum(((gwa := int64(5000000000) + i) for i in range(2)))  # tpyc: ok
print("genexpr_walrus:", gtot)
gcond = sum(i for i in range(3) if (k := i) > 0)  # tpyc: ok
print("genexpr_walrus cond:", gcond)


# genexpr_walrus_fn: the same in a function body
def genexpr_walrus_fn() -> None:
    ftot = sum(((fwa := 7 + i) for i in range(2)))  # tpyc: ok
    fcond = sum(i for i in range(3) if (fwk := i) > 0)  # tpyc: ok
    print("genexpr_walrus_fn:", ftot, fcond)


genexpr_walrus_fn()

# genexpr_walrus_shadow: a module function's own local of the target's name
# is another variable, so the frame binding stays unobservable
gsfound = any((gsy := x) > 4 for x in [1, 5, 3])  # tpyc: ok


def genexpr_walrus_shadow() -> int:
    gsy = 10
    return gsy


print("genexpr_walrus_shadow:", gsfound, genexpr_walrus_shadow())


# genexpr_walrus_sibling_fn: a sibling list comprehension's loop variable of
# the target's name is that comprehension's own
def genexpr_walrus_sibling_fn() -> None:
    sxs = [1, 5, 3]
    sws = [ssy for ssy in sxs]
    stot = sum((ssy := x) for x in sxs)  # tpyc: ok
    print("genexpr_walrus_sibling_fn:", sws, stot)


genexpr_walrus_sibling_fn()


# genexpr_walrus_own_name: a target named like the enclosing function is a
# new local of it, unused outside the generator expression
def genexpr_walrus_own_name() -> None:
    print("genexpr_walrus_own_name:", sum((genexpr_walrus_own_name := i + 1) for i in range(3)))  # tpyc: ok


genexpr_walrus_own_name()

# genexpr_walrus_write_only: sibling generator expressions that only store
# the target each write their own frame, at module level and in a function
wa = sum((gwo := x) for x in [1, 2])  # tpyc: ok
wb = sum((gwo := x * 2) for x in [1, 2])  # tpyc: ok
print("genexpr_walrus_write_only:", wa, wb)


def genexpr_walrus_write_only_fn() -> None:
    fa = sum((fwo := x) for x in [1, 2])  # tpyc: ok
    fb = sum((fwo := x * 2) for x in [1, 2])  # tpyc: ok
    print("genexpr_walrus_write_only_fn:", fa + fb)


genexpr_walrus_write_only_fn()

# walrus_global_fn: under `global`, a function's walrus writes the module
# variable
gn = 0


def walrus_global_fn() -> None:
    global gn
    print("walrus_global_fn:", (gn := 20))  # tpyc: ok


walrus_global_fn()
print("walrus_global_fn module:", gn)


# walrus_shadow_fn: a walrus in a function binds a local even where a module
# variable has the name, of the same type or another
gl = int64(0)
wsg = "gw"


def walrus_shadow_fn() -> None:
    print("walrus_shadow_fn:", (gl := int64(5000000000)))  # tpyc: ok
    print("walrus_shadow_fn:", gl)
    if (wsg := 3) > 1:  # tpyc: ok
        print("walrus_shadow_fn:", wsg)


walrus_shadow_fn()
print("walrus_shadow_fn module:", gl, wsg)


# loop_rebind_temp: a module str a loop over a temporary rebinds inside a
# block owns the last element, which dies with the temporary
def mk_strs() -> Own[list[str]]:
    return ["s" * 30, "t" * 30]


tv = "initial"
if True:
    for tv in mk_strs():  # tpyc: ok
        pass
tz = ["q" * 40, "r" * 40]
print("loop_rebind_temp:", tv)


def show_loop_rebind_temp() -> None:
    print("loop_rebind_temp reader:", tv)


show_loop_rebind_temp()


# loop_rebind_temp_fn: the same in a function body
def loop_rebind_temp_fn() -> None:
    fv = "initial"
    if True:
        for fv in mk_strs():  # tpyc: ok
            pass
    fz = ["q" * 40, "r" * 40]
    print("loop_rebind_temp_fn:", fv, len(fz))


loop_rebind_temp_fn()


# loop_rebind_temp_bytes: the bytes twin of loop_rebind_temp
def mk_bytes() -> Own[list[bytes]]:
    return [b"s" * 30, b"t" * 30]


tbv = b"initial"
if True:
    for tbv in mk_bytes():  # tpyc: ok
        pass
tbz = [b"q" * 40, b"r" * 40]
print("loop_rebind_temp_bytes:", tbv)


def show_loop_rebind_temp_bytes() -> None:
    print("loop_rebind_temp_bytes reader:", tbv)


show_loop_rebind_temp_bytes()

# unpack_name_str: str slots a tuple name unpacks into own their characters
# after the name is rebound
pt = ("k" * 30, 4)
us, un = pt  # tpyc: ok
pt = ("w" * 30, 5)
print("unpack_name_str:", us, un, pt[1])


def show_unpack_name_str() -> None:
    print("unpack_name_str reader:", us, un)


show_unpack_name_str()

# block_capture: a def in a module block reads names the block alone binds
# (locals of the module's code, no slot)
if True:
    blk = 5
    bks = "b" * 30

    def block_capture() -> None:
        print("block_capture:", blk, bks)  # tpyc: ok

    block_capture()
