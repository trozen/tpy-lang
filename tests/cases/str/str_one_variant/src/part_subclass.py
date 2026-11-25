# str() of a builtin's subclass, and a str subclass constructed at every
# expression position. The subclasses are immutable, so copy vs alias is not
# observable at any boundary here.
from typing import Any

from tpy import Stringable, String, StrView, char


class Text(str):
    pass


class MutText(String):
    pass


class Num(int):
    pass


class TextRepr(str):
    def __repr__(self) -> str:
        return "TextRepr-repr"


class NumRepr(int):
    def __repr__(self) -> str:
        return "NumRepr-repr"


class TextStr(str):
    def __str__(self) -> str:
        return "TextStr-str"


class NumStr(int):
    def __str__(self) -> str:
        return "NumStr-str"


class MutTextRepr(String):
    def __repr__(self) -> str:
        return "MutTextRepr-repr"


class Text2(Text):
    pass


class View(StrView):
    pass


class LetterStr(str):
    def __str__(self) -> char:
        return char("x")


class AnyStr(str):
    def __str__(self) -> Any:
        return "any-str"


class PlainLetter:
    def __str__(self) -> char:
        return char("p")


class PlainAny:
    def __repr__(self) -> Any:
        return "plain-any"


def gs[T: Stringable](x: T) -> str:
    return str(x)


def show(s: str) -> str:
    return s


def text() -> None:
    # a subclass inherits its base's text, not the default object form
    t = Text("payload")
    m = MutText("mutable")
    n = Num(42)
    print("sub text", str(t), str(m), str(n), gs(t), gs(m), gs(n))  # tpyc: ok
    print("sub fstr", f"{t}|{m}|{n}")
    # str.__str__ is the text, so a str subclass's own __repr__ is not str();
    # int has no __str__ of its own, so an int subclass's __repr__ is
    tr = TextRepr("abc")
    nr = NumRepr(7)
    print("sub repr", str(tr), str(nr), gs(tr), gs(nr), repr(tr), repr(nr))  # tpyc: ok
    # an own __str__ wins over the inherited text
    ts = TextStr("abc")
    ns = NumStr(7)
    print("sub own", str(ts), str(ns), gs(ts), gs(ns))  # tpyc: ok
    # a String subclass's own __repr__ is not str() either, nor is a str
    # subclass's subclass the default object form
    mr = MutTextRepr("mut")
    t2 = Text2("two")
    print("sub deep", str(mr), gs(mr), repr(mr), str(t2), gs(t2))  # tpyc: ok


def text_forms() -> None:
    # a StrView subclass inherits its base's text, as a str one does
    v = View("view")
    print("sub view", str(v), gs(v), f"{v}")  # tpyc: ok
    # an own __str__ returning a char or an Any is still the text, over the
    # subclass's own payload
    ls = LetterStr("a")
    an = AnyStr("b")
    print("sub char any", str(ls), str(an), gs(ls), gs(an))  # tpyc: ok
    # the same results on a plain class, through __str__ and __repr__
    pl = PlainLetter()
    pa = PlainAny()
    print("plain char any", str(pl), str(pa), gs(pl), gs(pa), f"{pl}|{pa}")  # tpyc: ok


def construction(s: str, cond: bool) -> None:
    # a str subclass constructed from each kind of str argument
    a = Text("hello")  # tpyc: ok
    b = Text(s + "!")  # tpyc: ok
    c = Text(f"[{s}]")  # tpyc: ok
    d = Text(s[0:2])  # tpyc: ok
    names = ["ab", "cd"]
    e = Text(names[0])  # tpyc: ok
    print("ctor", str(a), str(b), str(c), str(d), str(e), len(Text()))
    # a temporary at a call argument, a conditional and an `and` operand
    print("ctor temp", str(Text("temp")), show(Text("arg")))  # tpyc: ok
    t = Text("t") if cond else Text("f")  # tpyc: ok
    print("ctor ternary", str(t), t == "t")
    if cond and Text("a") == "a":  # tpyc: ok
        print("ctor and")
    # in a comprehension and a loop condition
    xs = [Text(x) for x in names]  # tpyc: ok
    print("ctor comp", len(xs), str(xs[1]))
    k = 0
    while len(Text("ab")) > k:  # tpyc: ok
        k += 1
    print("ctor while", k)


def main() -> None:
    text()
    text_forms()
    construction("xyz", True)
