# The char a string index yields lands at the str-typed slots below, spelled as
# the one-char string CPython's `s[i]` already is. Each section prints its own
# name plus the sizes/contents that would expose a silent brace-init size
# constructor. The slots that still want an explicit `str(s[i])` are listed in
# BUGS.md#char-at-str-residual-slots.
from typing import Final, Iterator
from tpy import Own, String, StrView, char, int32

CHARS: Final[str] = " .,:;+*?%S#@"

# module-level position: a char at a top-level str slot
TOP: str = CHARS[9]  # tpyc: ok


class Tag:
    name: str

    # constructor position: a char at a str field, through the member-init driver
    def __init__(self, s: str) -> None:
        self.name = s[0]  # tpyc: ok

    # method position: a char at a str FIELD store
    def pick(self, s: str) -> None:
        self.name = s[0]  # tpyc: ok


def take_str(s: str) -> int:
    return len(s)


def take_own(s: Own[str]) -> int:
    return len(s)


def take_view(s: StrView) -> int:
    return len(s)


def ret_slot(i: int) -> str:
    # return position: the slot that always worked -- the regression guard
    return CHARS[i]  # tpyc: ok


def extend_loop() -> Own[list[str]]:
    lut: list[str] = []
    for it in range(12):
        lut += [CHARS[it]]  # tpyc: ok
    return lut


def gen_chars() -> Iterator[str]:
    # generator position: a char at the str yield slot
    for it in range(3):
        yield CHARS[it]  # tpyc: ok


def main() -> None:
    # free-function position: annotated list literal, several elements
    two: list[str] = [CHARS[1], CHARS[2]]  # tpyc: ok
    print("listlit", len(two), "".join(two))

    # the `+=` extend that used to append ord(c) empty strings
    lut = extend_loop()
    print("extend", len(lut), "".join(lut))

    # method args at the container's Own[str] element slots
    app: list[str] = []
    app.append(CHARS[1])  # tpyc: ok
    c = CHARS[2]  # tpyc: type(char)
    app.append(c)  # tpyc: ok
    app.insert(0, CHARS[3])  # tpyc: ok
    app[0] = CHARS[4]  # tpyc: ok
    print("methods", len(app), "".join(app))

    # set element and dict key/value slots
    st: set[str] = set()
    st.add(CHARS[5])  # tpyc: ok
    d: dict[str, str] = {}
    d[CHARS[6]] = CHARS[7]  # tpyc: ok
    print("setdict", len(st), len(d), d[CHARS[6]])

    # the dict DEFAULT slot: the stub declares `default: V` (a borrow), so a
    # char reaches it as the view and the owned value comes back from the
    # native. Each call is its own statement -- `pop` mutates, and C++ leaves
    # argument order unspecified
    dd: dict[str, str] = {CHARS[6]: CHARS[7]}
    hit = dd.get(CHARS[6], CHARS[1])  # tpyc: ok
    miss = dd.get(CHARS[0], CHARS[1])  # tpyc: ok
    print("getdef", hit, miss)
    pmiss = dd.pop(CHARS[0], CHARS[2])  # tpyc: ok
    phit = dd.pop(CHARS[6], CHARS[2])  # tpyc: ok
    print("popdef", pmiss, phit, len(dd))

    # the LITERAL element slots of the same two containers, and `del` --
    # the key the lookup and the erase both take through the same narrow
    slit: set[str] = {CHARS[1]}  # tpyc: ok
    slit |= {CHARS[2]}  # tpyc: ok
    dlit: dict[str, str] = {CHARS[3]: CHARS[4]}  # tpyc: ok
    del dlit[CHARS[3]]  # tpyc: ok
    print("literals", len(slit), len(dlit))

    # tuple-literal element at an annotated tuple[str, int32] slot
    pair: tuple[str, int32] = (CHARS[2], 1)  # tpyc: ok
    print("tuple", pair[0], pair[1])

    # local decl and the three parameter forms
    one: str = CHARS[8]  # tpyc: ok
    print("params", one, take_str(CHARS[9]), take_own(CHARS[10]),
          take_view(CHARS[11]))

    # field store through the constructor and through a method, the module
    # global, and the return slot
    t = Tag("ab")
    print("ctor", t.name, TOP)
    t.pick("xy")
    print("field", t.name, ret_slot(1))

    # a char-TYPED source, not a string index
    ch: char = "x"
    chars: list[str] = [ch]  # tpyc: ok
    print("charvar", len(chars), chars[0])

    # the String sibling of the str element slot
    owned: list[String] = [CHARS[1]]  # tpyc: ok
    print("string", len(owned), owned[0])

    # comprehension position
    comp: list[str] = [CHARS[i] for i in range(3)]  # tpyc: ok
    print("comp", len(comp), "".join(comp))

    # generator position
    out = ""
    for g in gen_chars():
        out += g
    print("gen", out)


main()
