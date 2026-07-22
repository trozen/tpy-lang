# tpy: ext_module
# Borrow-view field aliasing: a never-reassigned class-typed field crosses
# as an aliasing view -- via a public read-only getset attribute, a method
# return (plain and readonly), a property, the tp_iter slot, and a free
# function's param field, all deduped to the SAME view object through the
# per-module registry; the first field (at offset 0 of the holder's
# payload) gets its own correctly-typed view, never the holder itself, and
# nested first fields stack (Wrap -> Pack -> Cell share one address, three
# types). Attribute WRITES to a view getset and holder re-init under live
# views raise ext-only (see ext_checks.py). (An upcast field source -- a
# derived-typed field returned as the base -- stays on the copy path, but a
# runtime witness is blocked by the derived-typed-field emit-order bug in
# BUGS.md; the classifier's exact-match gate is comp-pinned instead.)
from tpy import Int32, readonly
from tpy.extern import export


@export
class Cell:
    n: Int32

    def __init__(self, n: Int32):
        self.n = n

    def __next__(self) -> Int32:
        if self.n <= 0:
            raise StopIteration
        self.n -= 1
        return self.n


@export
class Pack:
    first: Cell
    second: Cell

    def __init__(self, a: Int32, b: Int32):
        self.first = Cell(a)
        self.second = Cell(b)

    def get_first(self) -> Cell:
        return self.first

    def peek_first(self) -> "readonly[Cell]":
        return self.first

    def __iter__(self) -> Cell:
        return self.first

    @property
    def head(self) -> Cell:
        return self.first


@export
class PackSub(Pack):
    # No own members: the base-declared fields' views must work through a
    # derived instance (Instance<Pack> reads at the shared payload offset).
    pass


@export
class Wrap:
    _pack: Pack

    def __init__(self):
        self._pack = Pack(3, 4)

    def get_pack(self) -> Pack:
        return self._pack


@export
def first_of(p: Pack) -> Cell:
    return p.first
