# tpy: cpp_namespace("tpystd::re")
"""Regular expressions, CPython-compatible surface, backed by PCRE2.

Architecture:
  * `_bindings.pcre2` -- raw @native bindings to the PCRE2 C API.
  * this module  -- pure-TPy facade. Pattern / Match classes manage PCRE2
    handle lifetimes via __del__. All Python semantics (flag mapping, group
    accessors, sub/split logic, error model) live here, not in C++.

Slice 1 surface:
  * compile(), search(), match(), fullmatch(), findall(), sub(), split()
  * Pattern + Match classes with the methods above.
  * Flags: IGNORECASE, MULTILINE, DOTALL, VERBOSE, ASCII (CPython bit values).
  * `re.error` exception, raised on invalid patterns.

TODO(v2): items deferred to a follow-up slice -- each is independent:
  * `re.compile` cache. Needs module-level mutable state (see
    STDLIB_ROADMAP "stdlib-enablement workstream"). Until then,
    `re.search("...", s)` in a hot loop is slower than caching a Pattern.
  * Named-group accessors: `match.group("name")` / `match.groupdict()`.
    Needs PCRE2 nametable walk + named-group index lookup.
  * finditer as a true generator (currently returns list[Own[Match]]).
    Loses streaming; minor for typical use, real for huge subjects.
  * bytes input on the public API (today: str only). Pattern + Match
    accept str; PCRE2 internally takes uint8_t* + length, so the lower
    layer is bytes-ready.
  * Python -> PCRE2 syntax translator. Not required for the current
    surface (PCRE2 accepts Python's `(?P<name>...)` form natively for
    back-compat). Add when we hit a real divergence -- candidates:
    `\1`-style backrefs in `sub` replacements, `(?#comment)` syntax,
    a few Unicode property edge cases.
  * `count` arg on `Pattern.sub` / module-level `sub` (limit number of
    replacements). Naive impl: walk finditer + manual splice; cleaner:
    a single `pcre2_substitute` call in non-global mode, looped.
  * `Match.span` / `start` / `end` returning `int` (BigInt) instead of
    `Int32` to match CPython exactly. Today they return `Int32` -- in
    practice tuple printing happens to match CPython, but type inference
    for downstream code differs (e.g. arithmetic on Match.start() picks
    Int32 ops vs CPython's int).
  * `Pattern.__repr__` / `Match.__repr__` matching CPython's format
    (`re.compile('...')` / `<re.Match object; span=(a, b), match='...'>`).
    Blocked on a Python-style `repr(str)` helper (escape backslashes,
    quotes, non-printables) and a flag-bits-to-name decoder. Land together
    to avoid asymmetric support.
"""

from __future__ import annotations
from typing import Final, Optional
from tpy import (
    Int32, UInt8, UInt32, UInt64, Ptr, readonly, Own, String, nocopy,
)
from tpy.extern import cpp_template
from tpy.mem import UninitArrayStorage, UninitHeapStorage
from tpy.unsafe import unsafe_ptr, unsafe_cast, unsafe_load, unsafe_str_from_buf
from tpy import take_ptr

# Bare `0`/`1` flow through to `size_t` / `uint32_t` PCRE2 args because the
# compiler treats integer literals (and literal-seeded locals like
# `offset = 0`) as polymorphic enough to retro-fit unsigned targets when
# the value provably fits. The remaining `UInt64(...)` / `UInt32(...)`
# casts in this file are on `len(...)` results (BigInt) and other typed
# sources, where a runtime narrowing check is intentional.
# TODO(compiler): aliased import block here is a workaround for two related
# entries in BUGS.md:
#   * `from _bindings import pcre2` doesn't bind the submodule -- forces
#     `from _bindings.pcre2 import X` for every used symbol;
#   * `pcre2.MatchData` qualified type names rejected in annotations --
#     forces aliasing the types (`_PcreMatchData` etc.) so they're plain
#     names by the time annotations reference them.
# When either lands, this can collapse to `from _bindings import pcre2` +
# `pcre2.MatchData` / `pcre2.compile(...)` throughout the file.
from _bindings.pcre2 import (
    Code as _PcreCode,
    MatchData as _PcreMatchData,
    MatchContext as _PcreMatchContext,
    PCRE2_CASELESS, PCRE2_MULTILINE, PCRE2_DOTALL, PCRE2_EXTENDED, PCRE2_UTF, PCRE2_UCP,
    PCRE2_ANCHORED, PCRE2_ENDANCHORED,
    PCRE2_SUBSTITUTE_GLOBAL, PCRE2_SUBSTITUTE_OVERFLOW_LENGTH,
    PCRE2_JIT_COMPLETE,
    PCRE2_ERROR_NOMATCH, PCRE2_ERROR_NOMEMORY,
    PCRE2_UNSET,
    compile as _pcre_compile, code_free as _pcre_code_free,
    jit_compile as _pcre_jit_compile,
    get_error_message as _pcre_get_error_message,
    match_context_create as _pcre_mctx_create,
    match_context_free as _pcre_mctx_free,
    match_data_create_from_pattern as _pcre_md_create,
    match_data_free as _pcre_md_free,
    match as _pcre_match,
    get_ovector_pointer as _pcre_ovec,
    substitute as _pcre_substitute,
)


# ---------- RAII wrappers for PCRE2 handles ----------
# Every handle is wrapped in an @nocopy owning type so the corresponding
# free runs automatically on scope exit (returns, raises, field-holding
# record destruction). All three free functions are null-safe, so __del__
# needs no null check.
#
# @nocopy+__del__ wrappers have their auto default ctor suppressed, so
# the enclosing record's field init must MIL-hoist (RHS references only
# ctor params / module-level names, no body-locals). That's why _OwnedCode
# takes high-level args in __init__ and delegates the multi-step PCRE2
# call sequence to a staticmethod returning Ptr[_PcreCode] -- the
# dangling-return check trusts locals bound from call returns.


@nocopy
class _OwnedMatchData:
    _p: Ptr[_PcreMatchData]
    def __init__(self, p: Ptr[_PcreMatchData]) -> None:
        self._p = p
    def __del__(self) -> None:
        _pcre_md_free(self._p)
    def get(self) -> Ptr[_PcreMatchData]:
        return self._p


# ---------- re.error exception ----------
# Must be defined BEFORE _OwnedCode because its _compile staticmethod is
# emitted inline in the generated header and throws `error`, which
# requires a complete type at the throw site (not just a forward decl).
# See "User Exception subclass doesn't auto-inherit native __init__" in
# BUGS.md for why `__init__` is declared explicitly.
class error(Exception):
    """Raised when PCRE2 rejects a pattern at compile time, or hits a
    match-time error (rare). Catchable as a normal exception.

    Caller-side gap that still bites: `except re.error:` is blocked by
    the qualified-except-clause limitation (BUGS.md). Users today
    must `from re import error` (no alias) then `except error:`.
    """
    def __init__(self, message: str = "") -> None:
        super().__init__(message)


# Convert a PCRE2 negative error code into a human-readable message via
# pcre2_get_error_message. Buffer is stack-allocated 256 bytes via
# UninitArrayStorage -- RAII, no manual free.
def _pcre2_error_msg(errcode: Int32) -> str:
    buf = UninitArrayStorage[UInt8, 256]()
    n = _pcre_get_error_message(errcode, buf.ptr(), 256)
    if n < 0:
        return "unknown error"
    return unsafe_str_from_buf(unsafe_cast(buf.ptr()), UInt64(n))


@nocopy
class _OwnedCode:
    _p: Ptr[_PcreCode]
    def __init__(self, pattern: str, flags: Int32) -> None:
        self._p = _OwnedCode._compile(pattern, flags)
    def __del__(self) -> None:
        _pcre_code_free(self._p)
    def get(self) -> Ptr[_PcreCode]:
        return self._p

    @staticmethod
    def _compile(pattern: str, flags: Int32) -> Ptr[_PcreCode]:
        errcode: Int32 = 0
        erroff: UInt64 = 0
        opts = _to_pcre2_opts(flags)
        p_data: Ptr[readonly[UInt8]] = unsafe_cast(unsafe_ptr(pattern))
        code = _pcre_compile(p_data, UInt64(len(pattern)),
                             opts, take_ptr(errcode), take_ptr(erroff),
                             None)
        if code is None:
            msg = _pcre2_error_msg(errcode)
            raise error(f"compile error at offset {erroff}: {msg}")
        return code


@nocopy
class _OwnedMatchContext:
    _p: Ptr[_PcreMatchContext]
    def __init__(self) -> None:
        self._p = _pcre_mctx_create(None)
    def __del__(self) -> None:
        _pcre_mctx_free(self._p)
    def get(self) -> Ptr[_PcreMatchContext]:
        return self._p


# ---------- Public flag constants (CPython-compatible bit values) ----------

# User-facing flags are `Int32`: the total bit surface is tiny (max 256),
# negative values are never valid, and Int32 is TPy's DefaultInt so users
# don't need to write `UInt32(...)` when mixing flags with bare literals.
# Internally `_to_pcre2_opts` translates to PCRE2's `UInt32` flag space
# where top-bit values like PCRE2_ANCHORED require the wider unsigned range.
NOFLAG:     Final[Int32] = 0
IGNORECASE: Final[Int32] = 2
MULTILINE:  Final[Int32] = 8
DOTALL:     Final[Int32] = 16
VERBOSE:    Final[Int32] = 64
ASCII:      Final[Int32] = 256

# Short aliases (CPython exposes both forms).
I: Final[Int32] = IGNORECASE
M: Final[Int32] = MULTILINE
S: Final[Int32] = DOTALL
X: Final[Int32] = VERBOSE
A: Final[Int32] = ASCII


def _to_pcre2_opts(flags: Int32) -> UInt32:
    """Translate `re.*` flag bits to PCRE2 option bits.

    UTF + UCP are on by default (matches CPython str-mode regex behavior:
    Unicode-aware \\d, \\w, \\s, case-folding). `re.ASCII` opts out.
    """
    opts: UInt32 = PCRE2_UTF | PCRE2_UCP
    if (flags & IGNORECASE) != 0:
        opts |= PCRE2_CASELESS
    if (flags & MULTILINE) != 0:
        opts |= PCRE2_MULTILINE
    if (flags & DOTALL) != 0:
        opts |= PCRE2_DOTALL
    if (flags & VERBOSE) != 0:
        opts |= PCRE2_EXTENDED
    if (flags & ASCII) != 0:
        opts &= ~(PCRE2_UTF | PCRE2_UCP)
    return opts


# ---------- Match ----------

class Match:
    """Result of a successful match. Owns its PCRE2 match-data block (via
    the `_OwnedMatchData` wrapper -- freed automatically on destruction)
    and a copy of the subject string (so group() can return slices that
    outlive the originating user variable)."""

    _md: _OwnedMatchData
    _subject: str
    _ngroups: Int32   # number of populated entries in the ovector

    def __init__(self, md: Own[_OwnedMatchData], subject: str,
                 ngroups: Int32) -> None:
        self._md = md
        self._subject = subject
        self._ngroups = ngroups

    def _ovec_load(self, i: UInt32) -> UInt64:
        ovec = _pcre_ovec(self._md.get())
        return unsafe_load(ovec, i)

    def span(self, group: Int32 = 0) -> tuple[Int32, Int32]:
        """(start, end) byte offsets of `group` in the subject. (-1, -1)
        means the group did not participate.

        TODO(v2): non-participating group should surface as `(-1, -1)` to
        match CPython; today returns `(0, 0)`. Trivial to fix once the
        Match accessor APIs commit to either Int32 or BigInt return type
        (-1 needs a signed type)."""
        if group < 0 or group >= self._ngroups:
            raise error(f"no such group: {group}")
        start = self._ovec_load(UInt32.trunc(group * 2))
        end = self._ovec_load(UInt32.trunc(group * 2 + 1))
        if start == PCRE2_UNSET or end == PCRE2_UNSET:
            return (0, 0)
        return (Int32.trunc(start), Int32.trunc(end))

    def start(self, group: Int32 = 0) -> Int32:
        return self.span(group)[0]

    def end(self, group: Int32 = 0) -> Int32:
        return self.span(group)[1]

    def group(self, i: Int32 = 0) -> str:
        """Substring of the subject for `group` (0 = full match)."""
        s, e = self.span(i)
        return self._subject[s:e]

    def groups(self) -> Own[list[str]]:
        """All capture groups as a list (excluding group 0).

        TODO(v2): return `tuple[str, ...]` to match CPython instead of
        list. Blocked on varadic-tuple support in TPy."""
        out: list[str] = []
        for i in range(1, self._ngroups):
            out.append(self.group(i))
        return out


# ---------- Pattern ----------

class Pattern:
    """Compiled regex. Holds the PCRE2 code + match-context handles; the
    _OwnedCode / _OwnedMatchContext wrappers free them on destruction."""

    _code: _OwnedCode
    _mctx: _OwnedMatchContext
    pattern: str
    flags: Int32

    def __init__(self, pattern: str, flags: Int32 = NOFLAG) -> None:
        # Both field initializers reference only ctor params / module-level
        # names -- no body-locals -- so they MIL-hoist into move-construction
        # (safe on @nocopy+__del__ fields).
        self._code = _OwnedCode(pattern, flags)
        self._mctx = _OwnedMatchContext()
        # JIT-compile for ~10x match speedup. Failure here is non-fatal --
        # PCRE2 falls back to interpreted matching on patterns the JIT
        # can't handle.
        _pcre_jit_compile(self._code.get(), PCRE2_JIT_COMPLETE)
        self.pattern = pattern
        self.flags = flags

    def _do_match(self, subject: str, start_offset: UInt64,
                  opts: UInt32) -> Optional[Own[Match]]:
        md_raw = _pcre_md_create(self._code.get(), None)
        if md_raw is None:
            raise error("out of memory allocating match data")
        md = _OwnedMatchData(md_raw)
        s_data: Ptr[readonly[UInt8]] = unsafe_cast(unsafe_ptr(subject))
        rc = _pcre_match(self._code.get(), s_data, UInt64(len(subject)),
                         start_offset, opts, md.get(), self._mctx.get())
        if rc < 0:
            if rc == PCRE2_ERROR_NOMATCH:
                return None            # md drops here, frees automatically
            raise error(_pcre2_error_msg(rc))   # same
        return Match(md, subject, rc)           # md moves into the Match

    def search(self, subject: str) -> Optional[Own[Match]]:
        return self._do_match(subject, 0, 0)

    def match(self, subject: str) -> Optional[Own[Match]]:
        return self._do_match(subject, 0, PCRE2_ANCHORED)

    def fullmatch(self, subject: str) -> Optional[Own[Match]]:
        return self._do_match(subject, 0,
                              PCRE2_ANCHORED | PCRE2_ENDANCHORED)

    def finditer(self, subject: str) -> Own[list[Match]]:
        """All non-overlapping matches as a list.

        TODO(v2): return a true generator like CPython (lazy iteration).
        Today materializes the full list -- fine for typical cases, real
        memory cost on huge subjects."""
        out: list[Match] = []
        offset = 0
        sub_len = UInt64(len(subject))
        s_data: Ptr[readonly[UInt8]] = unsafe_cast(unsafe_ptr(subject))
        while offset <= sub_len:
            md_raw = _pcre_md_create(self._code.get(), None)
            if md_raw is None:
                raise error("out of memory allocating match data")
            md = _OwnedMatchData(md_raw)
            rc = _pcre_match(self._code.get(), s_data, sub_len, offset,
                             0, md.get(), self._mctx.get())
            if rc < 0:
                if rc == PCRE2_ERROR_NOMATCH:
                    break          # md drops at end of iteration
                raise error(_pcre2_error_msg(rc))   # md drops
            ovec = _pcre_ovec(md.get())
            mstart = unsafe_load(ovec, 0)
            mend = unsafe_load(ovec, 1)
            out.append(Match(md, subject, rc))   # md moves into Match
            # Bump-along by one byte on zero-width match to avoid an
            # infinite loop.
            if mend == mstart:
                offset = mend + 1
            else:
                offset = mend
        return out

    def findall(self, subject: str) -> Own[list[str]]:
        """All non-overlapping match strings (group 0).

        TODO(v2): for patterns with capture groups, CPython returns a list
        of capture-tuples (or single captures for one-group patterns), not
        the whole-match string. Today we always return group(0) regardless
        of pattern shape -- divergence flagged in `no_cpython.txt`."""
        out: list[str] = []
        # TODO(compiler): the natural form `for m in self.finditer(subject):`
        # fails C++ compilation -- "cannot bind non-const lvalue reference
        # to rvalue" -- because finditer returns `Own[list[Match]]`. See
        # BUGS.md entry "Iterating directly over a call that returns
        # Own[list[T]]". When the codegen fix lands (use `auto&&` for the
        # iteration temp on rvalue iterables), drop the `matches` local.
        matches = self.finditer(subject)
        for m in matches:
            out.append(m.group(Int32(0)))
        return out

    def sub(self, repl: str, subject: str) -> str:
        """Replace every match of the pattern in `subject` with `repl`.
        PCRE2-native backref syntax: $1..$9, ${name}.

        TODO(v2): support CPython's `count` parameter (limit number of
        replacements). Naive: walk finditer + manual splice; cleaner: loop
        `pcre2_substitute` in non-global mode `count` times.

        TODO(v2): translate CPython's `\\1`-style backref syntax in `repl`
        to PCRE2's `$1` so users can copy regex code over without
        rewriting replacements. Today `\\1` is treated as a literal."""
        sub_data: Ptr[readonly[UInt8]] = unsafe_cast(unsafe_ptr(subject))
        repl_data: Ptr[readonly[UInt8]] = unsafe_cast(unsafe_ptr(repl))
        # `outlen` is both input (buffer capacity PCRE2 reads on entry) and
        # output (actual bytes written / bytes needed). A plain local +
        # take_ptr avoids needing a separate single-cell storage object.
        # `outbuf` is heap-allocated via UninitHeapStorage -- freed on
        # scope exit, including the raise path and on retry-reassignment.
        # `cap` stays as an explicit UInt64(...) cast because len() returns
        # Int32; the signed->unsigned conversion isn't automatic in TPy
        # (cross-sign widening is unsigned->signed-only). See BUGS.md.
        cap = UInt64(len(subject) * 2 + len(repl) + 16)
        outlen: UInt64 = cap
        outbuf = UninitHeapStorage[UInt8](UInt32.trunc(cap))
        opts = PCRE2_SUBSTITUTE_GLOBAL | PCRE2_SUBSTITUTE_OVERFLOW_LENGTH
        rc = _pcre_substitute(
            self._code.get(), sub_data, UInt64(len(subject)),
            0, opts, None, self._mctx.get(),
            repl_data, UInt64(len(repl)),
            outbuf.ptr(), take_ptr(outlen),
        )
        if rc == PCRE2_ERROR_NOMEMORY:
            # PCRE2 wrote the required size into outlen. Reallocate outbuf
            # at that exact size (reassignment drops the old storage).
            # Drop OVERFLOW_LENGTH on retry: buffer is now correctly sized,
            # and asking for overflow-length again would make PCRE2 redo
            # the sizing pass for nothing.
            outbuf = UninitHeapStorage[UInt8](UInt32.trunc(outlen))
            rc = _pcre_substitute(
                self._code.get(), sub_data, UInt64(len(subject)),
                0, PCRE2_SUBSTITUTE_GLOBAL, None,
                self._mctx.get(), repl_data, UInt64(len(repl)),
                outbuf.ptr(), take_ptr(outlen),
            )
        if rc < 0:
            raise error(_pcre2_error_msg(rc))
        return unsafe_str_from_buf(unsafe_cast(outbuf.ptr()), outlen)

    def split(self, subject: str, maxsplit: Int32 = 0) -> Own[list[str]]:
        """Split `subject` at each match. `maxsplit=0` means no limit."""
        out: list[str] = []
        offset = 0
        splits: Int32 = 0
        sub_len = UInt64(len(subject))
        s_data: Ptr[readonly[UInt8]] = unsafe_cast(unsafe_ptr(subject))
        md_raw = _pcre_md_create(self._code.get(), None)
        if md_raw is None:
            raise error("out of memory allocating match data")
        md = _OwnedMatchData(md_raw)
        while offset <= sub_len:
            if maxsplit > Int32(0) and splits >= maxsplit:
                break
            rc = _pcre_match(self._code.get(), s_data, sub_len, offset,
                             0, md.get(), self._mctx.get())
            if rc < 0:
                if rc == PCRE2_ERROR_NOMATCH:
                    break
                raise error(_pcre2_error_msg(rc))   # md drops
            ovec = _pcre_ovec(md.get())
            mstart = unsafe_load(ovec, 0)
            mend = unsafe_load(ovec, 1)
            out.append(subject[Int32.trunc(offset):Int32.trunc(mstart)])
            if mend == mstart:
                offset = mend + 1
            else:
                offset = mend
            splits += Int32(1)
        out.append(subject[Int32.trunc(offset):])
        return out      # md drops at end of scope


# ---------- Module-level wrappers ----------
# CPython's `re.compile` returns a Pattern; we do the same.
# `re.search("...", subj)` etc. compile-and-throw-away -- the compile cache
# that CPython has needs module-level mutable state, deferred.

def compile(pattern: str, flags: Int32 = NOFLAG) -> Own[Pattern]:
    return Pattern(pattern, flags)

def search(pattern: str, subject: str,
           flags: Int32 = NOFLAG) -> Optional[Own[Match]]:
    return Pattern(pattern, flags).search(subject)

def match(pattern: str, subject: str,
          flags: Int32 = NOFLAG) -> Optional[Own[Match]]:
    return Pattern(pattern, flags).match(subject)

def fullmatch(pattern: str, subject: str,
              flags: Int32 = NOFLAG) -> Optional[Own[Match]]:
    return Pattern(pattern, flags).fullmatch(subject)

def findall(pattern: str, subject: str,
            flags: Int32 = NOFLAG) -> Own[list[str]]:
    return Pattern(pattern, flags).findall(subject)

def sub(pattern: str, repl: str, subject: str,
        flags: Int32 = NOFLAG) -> str:
    return Pattern(pattern, flags).sub(repl, subject)

def split(pattern: str, subject: str, maxsplit: Int32 = Int32(0),
          flags: Int32 = NOFLAG) -> Own[list[str]]:
    return Pattern(pattern, flags).split(subject, maxsplit)
