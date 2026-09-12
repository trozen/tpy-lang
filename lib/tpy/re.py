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
  * bytes input on the public API (today: str only). Pattern + Match
    accept str; PCRE2 internally takes uint8_t* + length, so the lower
    layer is bytes-ready.
  * Python -> PCRE2 syntax translator. Not required for the current
    surface (PCRE2 accepts Python's `(?P<name>...)` form natively for
    back-compat). Add when we hit a real divergence -- candidates:
    `\1`-style backrefs in `sub` replacements, `(?#comment)` syntax,
    a few Unicode property edge cases.
  * `Match.span` / `start` / `end` returning `int` (BigInt) instead of
    `int32` to match CPython exactly. Today they return `int32` -- in
    practice tuple printing happens to match CPython, but type inference
    for downstream code differs (e.g. arithmetic on Match.start() picks
    int32 ops vs CPython's int).
  * `Pattern.__repr__` / `Match.__repr__` matching CPython's format
    (`re.compile('...')` / `<re.Match object; span=(a, b), match='...'>`).
    Blocked on a Python-style `repr(str)` helper (escape backslashes,
    quotes, non-printables) and a flag-bits-to-name decoder. Land together
    to avoid asymmetric support.
"""

from __future__ import annotations
from typing import Final, Iterator, Optional
from tpy import (
    int32, uint8, uint32, uint64, Ptr, readonly, Own, String, nocopy,
)
from tpy.extern import cpp_template
from tpy.mem import UninitArrayStorage, UninitHeapStorage
from tpy.unsafe import unsafe_ptr, unsafe_cast, unsafe_load, unsafe_str_from_buf
from tpy import take_ptr

# Bare `0`/`1` flow through to `size_t` / `uint32_t` PCRE2 args because the
# compiler treats integer literals (and literal-seeded locals like
# `offset = 0`) as polymorphic enough to retro-fit unsigned targets when
# the value provably fits. The remaining `uint64(...)` / `uint32(...)`
# casts in this file are on `len(...)` results (BigInt) and other typed
# sources, where a runtime narrowing check is intentional.
from _bindings import pcre2


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
# call sequence to a staticmethod returning Ptr[pcre2.Code] -- the
# dangling-return check trusts locals bound from call returns.


@nocopy
class _OwnedMatchData:
    _p: Ptr[pcre2.MatchData]
    def __init__(self, p: Ptr[pcre2.MatchData]) -> None:
        self._p = p
    def __del__(self) -> None:
        pcre2.match_data_free(self._p)
    def get(self) -> Ptr[pcre2.MatchData]:
        return self._p


# ---------- re.error exception ----------
# Must be defined BEFORE _OwnedCode because its _compile staticmethod is
# emitted inline in the generated header and throws `error`, which
# requires a complete type at the throw site (not just a forward decl).
class error(Exception):
    """Raised when PCRE2 rejects a pattern at compile time, or hits a
    match-time error (rare). Catchable as a normal exception, either via
    `from re import error` then `except error:`, or qualified as
    `except re.error:`.
    """
    pass


# Convert a PCRE2 negative error code into a human-readable message via
# pcre2_get_error_message. Buffer is stack-allocated 256 bytes via
# UninitArrayStorage -- RAII, no manual free.
def _pcre2_error_msg(errcode: int32) -> str:
    buf = UninitArrayStorage[uint8, 256]()
    n = pcre2.get_error_message(errcode, buf.ptr(), 256)
    if n < 0:
        return "unknown error"
    return unsafe_str_from_buf(unsafe_cast(buf.ptr()), uint64(n))


def _utf8_advance(data: Ptr[readonly[uint8]], offset: uint64,
                  length: uint64) -> uint64:
    """Advance one UTF-8 character past `offset`. Empty-match bump-along
    cannot step a bare byte: PCRE2 runs in UTF mode by default and rejects
    an offset that lands mid-character. TPy str is well-formed UTF-8, so the
    lead byte alone determines the width. At/after the end, step one so the
    caller's loop still terminates."""
    if offset >= length:
        return offset + 1
    lead = unsafe_load(data, uint32.trunc(offset))
    if lead < 0xC0:        # ASCII (< 0x80) or a stray continuation byte
        return offset + 1
    if lead < 0xE0:
        return offset + 2
    if lead < 0xF0:
        return offset + 3
    if lead < 0xF8:
        return offset + 4
    return offset + 1      # invalid lead byte; defensive


@nocopy
class _OwnedCode:
    _p: Ptr[pcre2.Code]
    def __init__(self, pattern: str, flags: int32) -> None:
        self._p = _OwnedCode._compile(pattern, flags)
    def __del__(self) -> None:
        pcre2.code_free(self._p)
    def get(self) -> Ptr[pcre2.Code]:
        return self._p

    @staticmethod
    def _compile(pattern: str, flags: int32) -> Ptr[pcre2.Code]:
        errcode: int32 = 0
        erroff: uint64 = 0
        opts = _to_pcre2_opts(flags)
        p_data: Ptr[readonly[uint8]] = unsafe_cast(unsafe_ptr(pattern))
        code = pcre2.compile(p_data, uint64(len(pattern)),
                             opts, take_ptr(errcode), take_ptr(erroff),
                             None)
        if code is None:
            msg = _pcre2_error_msg(errcode)
            raise error(f"compile error at offset {erroff}: {msg}")
        return code


@nocopy
class _OwnedMatchContext:
    _p: Ptr[pcre2.MatchContext]
    def __init__(self) -> None:
        self._p = pcre2.match_context_create(None)
    def __del__(self) -> None:
        pcre2.match_context_free(self._p)
    def get(self) -> Ptr[pcre2.MatchContext]:
        return self._p


# ---------- Public flag constants (CPython-compatible bit values) ----------

# User-facing flags are `int32`: the total bit surface is tiny (max 256),
# negative values are never valid, and int32 is TPy's DefaultInt so users
# don't need to write `uint32(...)` when mixing flags with bare literals.
# Internally `_to_pcre2_opts` translates to PCRE2's `uint32` flag space
# where top-bit values like pcre2.PCRE2_ANCHORED require the wider unsigned range.
NOFLAG:     Final[int32] = 0
IGNORECASE: Final[int32] = 2
MULTILINE:  Final[int32] = 8
DOTALL:     Final[int32] = 16
VERBOSE:    Final[int32] = 64
ASCII:      Final[int32] = 256

# Short aliases (CPython exposes both forms).
I: Final[int32] = IGNORECASE
M: Final[int32] = MULTILINE
S: Final[int32] = DOTALL
X: Final[int32] = VERBOSE
A: Final[int32] = ASCII


def _to_pcre2_opts(flags: int32) -> uint32:
    """Translate `re.*` flag bits to PCRE2 option bits.

    UTF + UCP are on by default (matches CPython str-mode regex behavior:
    Unicode-aware \\d, \\w, \\s, case-folding). `re.ASCII` opts out.
    """
    opts: uint32 = pcre2.PCRE2_UTF | pcre2.PCRE2_UCP
    if (flags & IGNORECASE) != 0:
        opts |= pcre2.PCRE2_CASELESS
    if (flags & MULTILINE) != 0:
        opts |= pcre2.PCRE2_MULTILINE
    if (flags & DOTALL) != 0:
        opts |= pcre2.PCRE2_DOTALL
    if (flags & VERBOSE) != 0:
        opts |= pcre2.PCRE2_EXTENDED
    if (flags & ASCII) != 0:
        opts &= ~(pcre2.PCRE2_UTF | pcre2.PCRE2_UCP)
    return opts


# ---------- Match ----------

class Match:
    """Result of a successful match. Owns its PCRE2 match-data block (via
    the `_OwnedMatchData` wrapper -- freed automatically on destruction)
    and a copy of the subject string (so group() can return slices that
    outlive the originating user variable)."""

    _md: _OwnedMatchData
    _subject: str
    _ngroups: int32   # number of populated entries in the ovector

    def __init__(self, md: Own[_OwnedMatchData], subject: str,
                 ngroups: int32) -> None:
        self._md = md
        self._subject = subject
        self._ngroups = ngroups

    def _ovec_load(self, i: uint32) -> uint64:
        ovec = pcre2.get_ovector_pointer(self._md.get())
        return unsafe_load(ovec, i)

    def span(self, group: int32 = 0) -> tuple[int32, int32]:
        """(start, end) byte offsets of `group` in the subject. (-1, -1)
        means the group did not participate.

        TODO(v2): non-participating group should surface as `(-1, -1)` to
        match CPython; today returns `(0, 0)`. Trivial to fix once the
        Match accessor APIs commit to either int32 or BigInt return type
        (-1 needs a signed type)."""
        if group < 0 or group >= self._ngroups:
            raise error(f"no such group: {group}")
        start = self._ovec_load(uint32.trunc(group * 2))
        end = self._ovec_load(uint32.trunc(group * 2 + 1))
        if start == pcre2.PCRE2_UNSET or end == pcre2.PCRE2_UNSET:
            return (0, 0)
        return (int32.trunc(start), int32.trunc(end))

    def start(self, group: int32 = 0) -> int32:
        return self.span(group)[0]

    def end(self, group: int32 = 0) -> int32:
        return self.span(group)[1]

    def group(self, i: int32 = 0) -> str:
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
    flags: int32

    def __init__(self, pattern: str, flags: int32 = NOFLAG) -> None:
        # Both field initializers reference only ctor params / module-level
        # names -- no body-locals -- so they MIL-hoist into move-construction
        # (safe on @nocopy+__del__ fields).
        self._code = _OwnedCode(pattern, flags)
        self._mctx = _OwnedMatchContext()
        # JIT-compile for ~10x match speedup. Failure here is non-fatal --
        # PCRE2 falls back to interpreted matching on patterns the JIT
        # can't handle.
        pcre2.jit_compile(self._code.get(), pcre2.PCRE2_JIT_COMPLETE)
        self.pattern = pattern
        self.flags = flags

    def _do_match(self, subject: str, start_offset: uint64,
                  opts: uint32) -> Optional[Own[Match]]:
        md_raw = pcre2.match_data_create_from_pattern(self._code.get(), None)
        if md_raw is None:
            raise error("out of memory allocating match data")
        md = _OwnedMatchData(md_raw)
        s_data: Ptr[readonly[uint8]] = unsafe_cast(unsafe_ptr(subject))
        rc = pcre2.match(self._code.get(), s_data, uint64(len(subject)),
                         start_offset, opts, md.get(), self._mctx.get())
        if rc < 0:
            if rc == pcre2.PCRE2_ERROR_NOMATCH:
                return None            # md drops here, frees automatically
            raise error(_pcre2_error_msg(rc))   # same
        return Match(md, subject, rc)           # md moves into the Match

    def search(self, subject: str) -> Optional[Own[Match]]:
        return self._do_match(subject, 0, 0)

    def match(self, subject: str) -> Optional[Own[Match]]:
        return self._do_match(subject, 0, pcre2.PCRE2_ANCHORED)

    def fullmatch(self, subject: str) -> Optional[Own[Match]]:
        return self._do_match(subject, 0,
                              pcre2.PCRE2_ANCHORED | pcre2.PCRE2_ENDANCHORED)

    def finditer(self, subject: str) -> Iterator[Own[Match]]:
        """All non-overlapping matches, yielded lazily (like CPython).

        Yields `Own[Match]` -- a Match owns its PCRE2 match-data, so it
        moves out of the generator by value rather than borrowing a frame
        local."""
        offset: uint64 = 0
        sub_len = uint64(len(subject))
        s_data: Ptr[readonly[uint8]] = unsafe_cast(unsafe_ptr(subject))
        while offset <= sub_len:
            md_raw = pcre2.match_data_create_from_pattern(self._code.get(), None)
            if md_raw is None:
                raise error("out of memory allocating match data")
            md = _OwnedMatchData(md_raw)
            rc = pcre2.match(self._code.get(), s_data, sub_len, offset,
                             0, md.get(), self._mctx.get())
            if rc < 0:
                if rc == pcre2.PCRE2_ERROR_NOMATCH:
                    break          # md drops at end of iteration
                raise error(_pcre2_error_msg(rc))   # md drops
            ovec = pcre2.get_ovector_pointer(md.get())
            mstart = unsafe_load(ovec, 0)
            mend = unsafe_load(ovec, 1)
            # mstart/mend are read before the yield moves `md` into the
            # Match, so the post-resume bump-along still has the offsets.
            yield Match(md, subject, rc)
            # Bump-along on zero-width match to avoid an infinite loop.
            if mend == mstart:
                offset = _utf8_advance(s_data, mend, sub_len)
            else:
                offset = mend

    def findall(self, subject: str) -> Own[list[str]]:
        """All non-overlapping match strings (group 0).

        TODO(v2): for patterns with capture groups, CPython returns a list
        of capture-tuples (or single captures for one-group patterns), not
        the whole-match string. Today we always return group(0) regardless
        of pattern shape -- divergence flagged in `no_cpython.txt`."""
        out: list[str] = []
        for m in self.finditer(subject):
            out.append(m.group(int32(0)))
        return out

    def _substitute(self, repl: str, subject: str, opts: uint32,
                    md: Ptr[pcre2.MatchData]) -> str:
        """One pcre2_substitute call (plus the buffer-resize retry).
        `opts` selects the mode: SUBSTITUTE_GLOBAL replaces every match;
        SUBSTITUTE_MATCHED replaces exactly the match already sitting in
        `md` (pass None for md in global mode)."""
        sub_data: Ptr[readonly[uint8]] = unsafe_cast(unsafe_ptr(subject))
        repl_data: Ptr[readonly[uint8]] = unsafe_cast(unsafe_ptr(repl))
        # `outlen` is both input (buffer capacity PCRE2 reads on entry) and
        # output (actual bytes written / bytes needed). A plain local +
        # take_ptr avoids needing a separate single-cell storage object.
        # `outbuf` is heap-allocated via UninitHeapStorage -- freed on
        # scope exit, including the raise path and on retry-reassignment.
        # `cap` stays as an explicit uint64(...) cast because len() returns
        # int32; the signed->unsigned conversion isn't automatic in TPy
        # (cross-sign widening is unsigned->signed-only). See BUGS.md.
        cap = uint64(len(subject) * 2 + len(repl) + 16)
        outlen: uint64 = cap
        outbuf = UninitHeapStorage[uint8](uint32.trunc(cap))
        rc = pcre2.substitute(
            self._code.get(), sub_data, uint64(len(subject)),
            0, opts | pcre2.PCRE2_SUBSTITUTE_OVERFLOW_LENGTH, md,
            self._mctx.get(), repl_data, uint64(len(repl)),
            outbuf.ptr(), take_ptr(outlen),
        )
        if rc == pcre2.PCRE2_ERROR_NOMEMORY:
            # PCRE2 wrote the required size into outlen. Reallocate outbuf
            # at that exact size (reassignment drops the old storage).
            # Drop OVERFLOW_LENGTH on retry: buffer is now correctly sized,
            # and asking for overflow-length again would make PCRE2 redo
            # the sizing pass for nothing.
            outbuf = UninitHeapStorage[uint8](uint32.trunc(outlen))
            rc = pcre2.substitute(
                self._code.get(), sub_data, uint64(len(subject)),
                0, opts, md,
                self._mctx.get(), repl_data, uint64(len(repl)),
                outbuf.ptr(), take_ptr(outlen),
            )
        if rc < 0:
            raise error(_pcre2_error_msg(rc))
        return unsafe_str_from_buf(unsafe_cast(outbuf.ptr()), outlen)

    def sub(self, repl: str, subject: str, count: int32 = 0) -> str:
        """Replace matches of the pattern in `subject` with `repl`.
        PCRE2-native backref syntax: $1..$9, ${name}. `count` limits the
        number of replacements; 0 replaces all, negative replaces none
        (CPython behavior).

        TODO(v2): translate CPython's `\\1`-style backref syntax in `repl`
        to PCRE2's `$1` so users can copy regex code over without
        rewriting replacements. Today `\\1` is treated as a literal."""
        if count == 0:
            return self._substitute(
                repl, subject, pcre2.PCRE2_SUBSTITUTE_GLOBAL, None)
        if count < 0:
            return subject
        md_raw = pcre2.match_data_create_from_pattern(self._code.get(), None)
        if md_raw is None:
            raise error("out of memory allocating match data")
        md = _OwnedMatchData(md_raw)
        # Match-then-substitute loop, one replacement per iteration, on a
        # working copy that grows/shrinks as replacements land. Advancement
        # mirrors PCRE2's own global-substitute algorithm (which matches
        # CPython 3.7+): after an empty match, first retry a NON-empty
        # match anchored at the same position, and only then skip one
        # character forward.
        result: str = subject
        offset: uint64 = 0
        remaining = count
        prev_empty = False
        while remaining > 0:
            s_len = uint64(len(result))
            if offset > s_len:
                break
            s_data: Ptr[readonly[uint8]] = unsafe_cast(unsafe_ptr(result))
            mopts: uint32 = 0
            if prev_empty:
                mopts = pcre2.PCRE2_NOTEMPTY_ATSTART | pcre2.PCRE2_ANCHORED
            rc = pcre2.match(self._code.get(), s_data, s_len, offset,
                             mopts, md.get(), self._mctx.get())
            if rc < 0:
                if rc == pcre2.PCRE2_ERROR_NOMATCH:
                    if prev_empty:
                        offset = _utf8_advance(s_data, offset, s_len)
                        prev_empty = False
                        continue
                    break
                raise error(_pcre2_error_msg(rc))
            ovec = pcre2.get_ovector_pointer(md.get())
            mstart = unsafe_load(ovec, 0)
            mend = unsafe_load(ovec, 1)
            old_len = s_len
            result = self._substitute(
                repl, result, pcre2.PCRE2_SUBSTITUTE_MATCHED, md.get())
            # Next attempt starts right after the replacement text. The
            # add-before-subtract order keeps the unsigned arithmetic
            # non-negative when the replacement shrinks the string.
            offset = mend + uint64(len(result)) - old_len
            prev_empty = mend == mstart
            remaining -= 1
        return result      # md drops at end of scope

    def split(self, subject: str, maxsplit: int32 = 0) -> Own[list[str]]:
        """Split `subject` at each match. `maxsplit=0` means no limit.

        Driven off finditer: each piece is the text between the previous
        match's end and the current match's start. Routing through finditer
        keeps zero-width matches enumerating the same positions CPython
        splits at -- a hand-rolled bump-along here would slice from the
        advanced scan offset and silently drop the inter-match text."""
        out: list[str] = []
        last: int32 = 0
        splits: int32 = 0
        for m in self.finditer(subject):
            if maxsplit > int32(0) and splits >= maxsplit:
                break
            out.append(subject[last:m.start()])
            last = m.end()
            splits += int32(1)
        out.append(subject[last:])
        return out


# ---------- Module-level wrappers ----------
# CPython's `re.compile` returns a Pattern; we do the same.
# `re.search("...", subj)` etc. compile-and-throw-away -- the compile cache
# that CPython has needs module-level mutable state, deferred.

def compile(pattern: str, flags: int32 = NOFLAG) -> Own[Pattern]:
    return Pattern(pattern, flags)

def search(pattern: str, subject: str,
           flags: int32 = NOFLAG) -> Optional[Own[Match]]:
    return Pattern(pattern, flags).search(subject)

def match(pattern: str, subject: str,
          flags: int32 = NOFLAG) -> Optional[Own[Match]]:
    return Pattern(pattern, flags).match(subject)

def fullmatch(pattern: str, subject: str,
              flags: int32 = NOFLAG) -> Optional[Own[Match]]:
    return Pattern(pattern, flags).fullmatch(subject)

def findall(pattern: str, subject: str,
            flags: int32 = NOFLAG) -> Own[list[str]]:
    return Pattern(pattern, flags).findall(subject)

def sub(pattern: str, repl: str, subject: str, count: int32 = 0,
        flags: int32 = NOFLAG) -> str:
    return Pattern(pattern, flags).sub(repl, subject, count)

def split(pattern: str, subject: str, maxsplit: int32 = int32(0),
          flags: int32 = NOFLAG) -> Own[list[str]]:
    return Pattern(pattern, flags).split(subject, maxsplit)
