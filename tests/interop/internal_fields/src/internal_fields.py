# tpy: ext_module
# Internal (`_`-prefixed) fields: a field whose name starts with `_` stays live
# C++ payload state but never crosses the CPython boundary as an attribute --
# uniform across BOTH attribute sites (exposed-class getset and exception
# data-fields). This lets an exposed class hold members that otherwise could not
# cross at all: `_secret` (a scalar the class keeps private), `_log` (a
# reference-class member -- a public reference-class field is rejected, but the
# internal form is allowed and reached only through methods), and `_cell` (a
# @nocopy move-only member, which also makes Vault itself move-only -- returning
# it via make_vault exercises instance_to_py's nothrow_move requirement on an
# enclosing class whose movability comes from an internal field). The same rule
# hides an exception's internal fields, including one (`_trace: list[int32]`) of a
# container type a PUBLIC exc data field cannot be; an exception whose fields are
# ALL internal falls back to the message-only setter. The hiding is an
# acknowledged divergence: under plain Python `_secret`/`_log`/`_cell`/`_trace`/
# `_note` are ordinary attributes, so the driver never reads them; ext_checks.py
# asserts their absence on the `.so`.
from tpy import int32, int64, Own, nocopy
from tpy.extern import export


@export
class Entry:
    v: int64

    def __init__(self, v: int64):
        self.v = v


@nocopy
class Cell:
    # A move-only (@nocopy) type held as an internal member: it makes Vault
    # itself move-only, so returning Vault across the boundary exercises
    # instance_to_py's nothrow_move requirement on an enclosing class whose
    # movability comes from an internal field.
    n: int64

    def __init__(self, n: int64):
        self.n = n


@export
class Vault:
    owner: int64        # public -- crosses as a getset
    _secret: int64      # internal scalar -- payload only
    _log: Entry         # internal reference-class member -- payload only
    _cell: Cell         # internal @nocopy member -- payload only, move-only

    def __init__(self, owner: int64, secret: int64):
        self.owner = owner
        self._secret = secret
        self._log = Entry(secret)
        self._cell = Cell(secret)

    def reveal(self) -> int64:
        return self._secret

    def cell_n(self) -> int64:
        return self._cell.n        # read the move-only internal member

    def record(self, v: int64) -> None:
        self._log.v = v            # mutate the internal member through self

    def snapshot(self) -> Own[Entry]:
        return Entry(self._log.v)  # honest copy-out via a method


@export
def make_vault(owner: int64, secret: int64) -> Own[Vault]:
    # Returns Vault across the boundary -> instantiates instance_to_py<Vault>,
    # whose nothrow_move static_assert must hold even though Vault is move-only
    # (its @nocopy Cell member deletes copy).
    return Vault(owner, secret)


class OpError(ValueError):
    code: int32          # public data field -- crosses as an attribute
    _trace: list[int32]  # internal -- and a container, which a PUBLIC exc data
                         # field cannot be (located error); internal is allowed

    def __init__(self, message: str, code: int32, trace: int32):
        super().__init__(message)
        self.code = code
        self._trace = [trace]


class SilentError(ValueError):
    # Every declared field is internal, so after the `_`-filter the exception
    # carries no crossing data and falls back to the message-only setter.
    _note: int32

    def __init__(self, message: str, note: int32):
        super().__init__(message)
        self._note = note


@export
def run(n: int64) -> int64:
    if n < 0:
        raise OpError("boom", 3, 999)
    if n == 0:
        raise SilentError("silent", 42)
    return n
