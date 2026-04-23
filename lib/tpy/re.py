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

# TODO(compiler): the `UInt64(0)` / `UInt32(0)` constructor calls scattered
# through this file (on call-site args to `_pcre_match`, `_pcre_substitute`,
# `_do_match`, etc.) are consistent with TPy's documented widening rules
# -- `Int32 -> UInt64` is deliberately not automatic because negative
# signed values don't round-trip through unsigned. This file surfaces the
# friction heavily because PCRE2's C API uses `size_t` / `uint32_t`
# throughout. See TODO.md entry on treating integer literals as untyped
# at call sites (Rust-style). When that lands, these constructors can
# collapse to bare `0`/`1`/`256` (the values are compile-time-known to
# fit the unsigned target).
# TODO(compiler): `Ptr[_PcreCompileContext]()` / `Ptr[_PcreGeneralContext]()`
# / `Ptr[_PcreMatchData]()` at the 4 call sites that pass a null
# context-pointer to PCRE2 are a workaround for TODO.md Bugs "None as a
# value for a Ptr[X]-typed parameter should codegen to nullptr". Today,
# typing the binding param as `Ptr[X] | None` codegens `std::optional<X*>`
# (breaks the C ABI), and bare `None` isn't accepted for `Ptr[X]` params.
# Default-constructing `Ptr[X]()` gives nullptr (per _containers.py), so
# that's what we pass. When sema accepts `None` as a Ptr value, collapse
# all four sites to just `None`.
# TODO(compiler): aliased import block here is a workaround for two related
# gaps in TODO.md "Bugs":
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
    CompileContext as _PcreCompileContext,
    GeneralContext as _PcreGeneralContext,
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


# ---------- RAII wrapper for pcre2_match_data ----------
# Match-data handles are created/destroyed in every match call (_do_match,
# finditer, split) -- dozens of error paths where a manual _pcre_md_free
# could be forgotten. Wrapping the pointer in an @nocopy owning type makes
# the free automatic on scope exit, including implicit drops on return /
# raise. PCRE2's _pcre_md_free is null-safe, so no null check in __del__.
#
# Pattern's `_code` / `_mctx` stay as raw Ptr with explicit free in
# Pattern.__del__: they're created once in __init__ and freed once in
# __del__, no error paths. Adding wrappers for them would hit a TPy
# codegen bug where nocopy-field initializers that reference local vars
# get lifted into the C++ MIL (where the locals are out of scope).
@nocopy
class _OwnedMatchData:
    _p: Ptr[_PcreMatchData]
    def __init__(self, p: Ptr[_PcreMatchData]) -> None:
        self._p = p
    def __del__(self) -> None:
        _pcre_md_free(self._p)
    def get(self) -> Ptr[_PcreMatchData]:
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


# ---------- re.error exception ----------

class error(Exception):
    """Raised when PCRE2 rejects a pattern at compile time, or hits a
    match-time error (rare). Catchable as a normal exception.

    Two compiler-gap workarounds in this declaration, each with its own
    cleanup path:

      1. The `__init__` is declared explicitly. CPython auto-inherits
         `Exception.__init__`; TPy doesn't yet -- see TODO.md Bugs
         "User Exception subclass doesn't auto-inherit native __init__".
         When fixed, drop this method and `class error(Exception): pass`
         is enough.

      2. The parameter is typed `String` (std::string) rather than `str`
         (std::string_view). The inherited `tpy::Exception(std::string)`
         C++ constructor has no string_view overload, so calling
         `super().__init__(message)` with a string_view fails to compile.
         When that's fixed (either by adding a string_view-taking
         constructor in core.hpp, or by codegen materializing
         string_view -> string at the super() call site), this can become
         the more idiomatic `def __init__(self, message: str = "")`.

    Caller-side gap that still bites: `except re.error:` is blocked by
    the qualified-except-clause limitation (TODO.md Bugs). Users today
    must `from re import error` (no alias) then `except error:`.
    """
    def __init__(self, message: String = "") -> None:
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
    """Compiled regex. Holds the PCRE2 code + match context handles, freed
    in __del__."""

    _code: Ptr[_PcreCode]
    _mctx: Ptr[_PcreMatchContext]
    pattern: str
    flags: Int32

    # TODO(compiler): `flags: Int32 = 0` should read `= NOFLAG`
    # but Final-named-constant-as-default-arg-value is rejected by sema --
    # see "Final[X] = SOME_NAMED_CONST rejected as default-parameter value"
    # in TODO.md Bugs. Same for every other signature in this file.
    def __init__(self, pattern: str, flags: Int32 = 0) -> None:
        # Plain local vars serve as out-parameter slots for PCRE2 to write
        # the error code + offset on compile failure; take_ptr gives the
        # Ptr[T] without explicit storage allocation.
        errcode: Int32 = 0
        erroff: UInt64 = 0
        opts = _to_pcre2_opts(flags)
        p_data: Ptr[readonly[UInt8]] = unsafe_cast(unsafe_ptr(pattern))
        code = _pcre_compile(p_data, UInt64(len(pattern)),
                             opts, take_ptr(errcode), take_ptr(erroff),
                             Ptr[_PcreCompileContext]())
        if code is None:
            msg = _pcre2_error_msg(errcode)
            raise error(f"compile error at offset {erroff}: {msg}")
        self._code = code
        self._mctx = _pcre_mctx_create(Ptr[_PcreGeneralContext]())
        # JIT-compile for ~10x match speedup. Failure here is non-fatal --
        # PCRE2 falls back to interpreted matching on patterns the JIT
        # can't handle.
        _pcre_jit_compile(self._code, PCRE2_JIT_COMPLETE)
        self.pattern = pattern
        self.flags = flags

    def __del__(self) -> None:
        _pcre_mctx_free(self._mctx)
        _pcre_code_free(self._code)

    def _do_match(self, subject: str, start_offset: UInt64,
                  opts: UInt32) -> Optional[Own[Match]]:
        md_raw = _pcre_md_create(self._code,
                                 Ptr[_PcreGeneralContext]())
        if md_raw is None:
            raise error("out of memory allocating match data")
        md = _OwnedMatchData(md_raw)
        s_data: Ptr[readonly[UInt8]] = unsafe_cast(unsafe_ptr(subject))
        rc = _pcre_match(self._code, s_data, UInt64(len(subject)),
                         start_offset, opts, md.get(), self._mctx)
        if rc < 0:
            if rc == PCRE2_ERROR_NOMATCH:
                return None            # md drops here, frees automatically
            raise error(_pcre2_error_msg(rc))   # same
        return Match(md, subject, rc)           # md moves into the Match

    def search(self, subject: str) -> Optional[Own[Match]]:
        return self._do_match(subject, UInt64(0), UInt32(0))

    def match(self, subject: str) -> Optional[Own[Match]]:
        return self._do_match(subject, UInt64(0), PCRE2_ANCHORED)

    def fullmatch(self, subject: str) -> Optional[Own[Match]]:
        return self._do_match(subject, UInt64(0),
                              PCRE2_ANCHORED | PCRE2_ENDANCHORED)

    def finditer(self, subject: str) -> Own[list[Match]]:
        """All non-overlapping matches as a list.

        TODO(v2): return a true generator like CPython (lazy iteration).
        Today materializes the full list -- fine for typical cases, real
        memory cost on huge subjects."""
        out: list[Match] = []
        offset = UInt64(0)
        sub_len = UInt64(len(subject))
        s_data: Ptr[readonly[UInt8]] = unsafe_cast(unsafe_ptr(subject))
        while offset <= sub_len:
            md_raw = _pcre_md_create(self._code,
                                     Ptr[_PcreGeneralContext]())
            if md_raw is None:
                raise error("out of memory allocating match data")
            md = _OwnedMatchData(md_raw)
            rc = _pcre_match(self._code, s_data, sub_len, offset,
                             UInt32(0), md.get(), self._mctx)
            if rc < 0:
                if rc == PCRE2_ERROR_NOMATCH:
                    break          # md drops at end of iteration
                raise error(_pcre2_error_msg(rc))   # md drops
            ovec = _pcre_ovec(md.get())
            mstart = unsafe_load(ovec, 0)
            mend = unsafe_load(ovec, UInt32(1))
            out.append(Match(md, subject, rc))   # md moves into Match
            # Bump-along by one byte on zero-width match to avoid an
            # infinite loop.
            if mend == mstart:
                offset = mend + UInt64(1)
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
        # TODO.md Bugs entry "Iterating directly over a call that returns
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
        # (cross-sign widening is unsigned->signed-only). See TODO.md.
        cap = UInt64(len(subject) * 2 + len(repl) + 16)
        outlen: UInt64 = cap
        outbuf = UninitHeapStorage[UInt8](UInt32.trunc(cap))
        opts = PCRE2_SUBSTITUTE_GLOBAL | PCRE2_SUBSTITUTE_OVERFLOW_LENGTH
        rc = _pcre_substitute(
            self._code, sub_data, UInt64(len(subject)),
            UInt64(0), opts, Ptr[_PcreMatchData](), self._mctx,
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
                self._code, sub_data, UInt64(len(subject)),
                UInt64(0), PCRE2_SUBSTITUTE_GLOBAL, Ptr[_PcreMatchData](),
                self._mctx, repl_data, UInt64(len(repl)),
                outbuf.ptr(), take_ptr(outlen),
            )
        if rc < 0:
            raise error(_pcre2_error_msg(rc))
        return unsafe_str_from_buf(unsafe_cast(outbuf.ptr()), outlen)

    def split(self, subject: str, maxsplit: Int32 = 0) -> Own[list[str]]:
        """Split `subject` at each match. `maxsplit=0` means no limit."""
        out: list[str] = []
        offset = UInt64(0)
        splits: Int32 = 0
        sub_len = UInt64(len(subject))
        s_data: Ptr[readonly[UInt8]] = unsafe_cast(unsafe_ptr(subject))
        md_raw = _pcre_md_create(self._code,
                                 Ptr[_PcreGeneralContext]())
        if md_raw is None:
            raise error("out of memory allocating match data")
        md = _OwnedMatchData(md_raw)
        while offset <= sub_len:
            if maxsplit > Int32(0) and splits >= maxsplit:
                break
            rc = _pcre_match(self._code, s_data, sub_len, offset,
                             UInt32(0), md.get(), self._mctx)
            if rc < 0:
                if rc == PCRE2_ERROR_NOMATCH:
                    break
                raise error(_pcre2_error_msg(rc))   # md drops
            ovec = _pcre_ovec(md.get())
            mstart = unsafe_load(ovec, 0)
            mend = unsafe_load(ovec, UInt32(1))
            # TODO(compiler): the `String(...)` wraps are a workaround for
            # list[str].append(strview) not materializing implicit
            # string_view -> std::string at the append call site. See
            # TODO.md "list[str].append(strview) fails to materialize...".
            # Once fixed, drop to `out.append(subject[...])`.
            out.append(String(subject[Int32.trunc(offset):Int32.trunc(mstart)]))
            if mend == mstart:
                offset = mend + UInt64(1)
            else:
                offset = mend
            splits += Int32(1)
        out.append(String(subject[Int32.trunc(offset):]))
        return out      # md drops at end of scope


# ---------- Module-level wrappers ----------
# CPython's `re.compile` returns a Pattern; we do the same.
# `re.search("...", subj)` etc. compile-and-throw-away -- the compile cache
# that CPython has needs module-level mutable state, deferred.

def compile(pattern: str, flags: Int32 = 0) -> Own[Pattern]:
    return Pattern(pattern, flags)

def search(pattern: str, subject: str,
           flags: Int32 = 0) -> Optional[Own[Match]]:
    return Pattern(pattern, flags).search(subject)

def match(pattern: str, subject: str,
          flags: Int32 = 0) -> Optional[Own[Match]]:
    return Pattern(pattern, flags).match(subject)

def fullmatch(pattern: str, subject: str,
              flags: Int32 = 0) -> Optional[Own[Match]]:
    return Pattern(pattern, flags).fullmatch(subject)

def findall(pattern: str, subject: str,
            flags: Int32 = 0) -> Own[list[str]]:
    return Pattern(pattern, flags).findall(subject)

def sub(pattern: str, repl: str, subject: str,
        flags: Int32 = 0) -> str:
    return Pattern(pattern, flags).sub(repl, subject)

def split(pattern: str, subject: str, maxsplit: Int32 = Int32(0),
          flags: Int32 = 0) -> Own[list[str]]:
    return Pattern(pattern, flags).split(subject, maxsplit)
