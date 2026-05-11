# tpy: cpp_namespace("tpystd::_bindings::pcre2")
# tpy: include("<tpy/stdlib/pcre2_h.hpp>")
# tpy: link("pcre2", managed=True)
"""Raw PCRE2 C bindings.

One @native declaration per pcre2_*_8 primitive, matching the upstream C
ABI 1:1. No Python semantics.
Consumers (the public `re` facade) build user-visible Pattern / Match
classes on top of these raw handles, using __del__ for RAII over the
opaque pcre2_code* / pcre2_match_data* lifetimes.

Naming:
  - Opaque type markers: `Code`, `MatchData`, `MatchContext`. The TPy-level
    handle is `Ptr[Code]` etc., which codegens to `pcre2_code_8*`.
  - All function names mirror the PCRE2 C names with the `_8` suffix dropped.

Constants are hardcoded UInt32 values matching pcre2.h. PCRE2's flag values
are part of its public ABI and don't change between versions.
"""

from typing import Final
from tpy import Ptr, UInt8, UInt32, UInt64, Int32, readonly
from tpy.extern import native


# ---------- Opaque type markers ----------
# Never instantiated directly; only passed/returned as `Ptr[X]`. The empty
# native class binds the C struct name so `Ptr[Code]` becomes `pcre2_code_8*`
# -- declared as a global-scope forward struct + alias in
# `runtime/cpp/include/tpy/stdlib/pcre2_h.hpp` (matches upstream pcre2.h
# layout; the real struct definitions live in PCRE2's own .c files).

@native("::pcre2_code_8")
class Code: ...

@native("::pcre2_match_data_8")
class MatchData: ...

@native("::pcre2_match_context_8")
class MatchContext: ...

# Context types we never construct -- passed as bare `Ptr[...]` to the
# PCRE2 functions that accept an optional context override. Caller always
# passes `None` (= default contexts; `Ptr[T]` is already nullable).
@native("::pcre2_compile_context_8")
class CompileContext: ...

@native("::pcre2_general_context_8")
class GeneralContext: ...


# ---------- Compile / match flags (subset we actually use) ----------
# Names mirror upstream pcre2.h exactly. Safe because pcre2.h is never
# included in our generated TUs (see runtime/cpp/include/tpy/stdlib/pcre2_h.hpp
# header comment) -- the preprocessor never sees a `#define PCRE2_CASELESS`
# in the same TU as our constant declaration. Values are part of PCRE2's
# stable ABI.

PCRE2_CASELESS:                     Final[UInt32] = 0x00000008
PCRE2_MULTILINE:                    Final[UInt32] = 0x00000400
PCRE2_DOTALL:                       Final[UInt32] = 0x00000020
PCRE2_EXTENDED:                     Final[UInt32] = 0x00000080
PCRE2_UTF:                          Final[UInt32] = 0x00080000
PCRE2_UCP:                          Final[UInt32] = 0x00020000
PCRE2_ANCHORED:                     Final[UInt32] = 0x80000000
PCRE2_ENDANCHORED:                  Final[UInt32] = 0x20000000

# Match-step option for pcre2_substitute.
PCRE2_SUBSTITUTE_GLOBAL:            Final[UInt32] = 0x00000100
PCRE2_SUBSTITUTE_OVERFLOW_LENGTH:   Final[UInt32] = 0x00001000

# pcre2_jit_compile selector: full JIT for normal matching.
PCRE2_JIT_COMPLETE:                 Final[UInt32] = 0x00000001

# Pattern-info selectors for pcre2_pattern_info.
PCRE2_INFO_CAPTURECOUNT:            Final[UInt32] = 4

# pcre2_match return codes. NOMATCH means "no match" (not an error);
# NOMEMORY is returned by pcre2_substitute when the output buffer is
# too small AND PCRE2_SUBSTITUTE_OVERFLOW_LENGTH is set.
PCRE2_ERROR_NOMATCH:                Final[Int32] = -1
PCRE2_ERROR_NOMEMORY:               Final[Int32] = -48

# Sentinel meaning "this group did not participate". PCRE2_UNSET is
# (PCRE2_SIZE)~0u = SIZE_MAX in C; we compare against it via raw uint64.
PCRE2_UNSET:                        Final[UInt64] = 0xFFFFFFFFFFFFFFFF


# ---------- Compile / free ----------
# Every binding below is a pure `@native` 1:1 mirror of PCRE2's C ABI.
# Arguments match pcre2.h exactly. Context-pointer args
# (pcre2_compile_context*, pcre2_general_context*, etc.) are typed as
# bare `Ptr[X]`; callers pass `None` (codegens to `nullptr`).

@native("::pcre2_compile_8")
def compile(pattern_data: Ptr[readonly[UInt8]], pattern_len: UInt64,
            flags: UInt32,
            errcode_out: Ptr[Int32], erroff_out: Ptr[UInt64],
            ccontext: Ptr[CompileContext]) -> Ptr[Code]: ...

@native("::pcre2_code_free_8")
def code_free(code: Ptr[Code]) -> None: ...

@native("::pcre2_jit_compile_8")
def jit_compile(code: Ptr[Code], opts: UInt32) -> Int32: ...

# Render a PCRE2 numeric error into a human-readable message. Writes into
# the caller-provided buffer and returns the number of bytes written
# (negative on error). `PCRE2_UCHAR8` is `uint8_t` -- same as `Ptr[UInt8]`'s
# underlying C type, so no cast needed.
@native("::pcre2_get_error_message_8")
def get_error_message(errcode: Int32, buf: Ptr[UInt8], buflen: UInt64) -> Int32: ...


# ---------- Match contexts and match-data blocks ----------

@native("::pcre2_match_context_create_8")
def match_context_create(
    gcontext: Ptr[GeneralContext]) -> Ptr[MatchContext]: ...

@native("::pcre2_match_context_free_8")
def match_context_free(mctx: Ptr[MatchContext]) -> None: ...

@native("::pcre2_match_data_create_from_pattern_8")
def match_data_create_from_pattern(
    code: Ptr[Code],
    gcontext: Ptr[GeneralContext]) -> Ptr[MatchData]: ...

@native("::pcre2_match_data_free_8")
def match_data_free(md: Ptr[MatchData]) -> None: ...


# ---------- Matching ----------

# pcre2_match returns >0 on success (count of matched groups including
# group 0), 0 if the ovector was too small (we always size correctly via
# match_data_create_from_pattern), or negative on no-match / error.
@native("::pcre2_match_8")
def match(code: Ptr[Code],
          subject_data: Ptr[readonly[UInt8]], subject_len: UInt64,
          start_offset: UInt64, opts: UInt32,
          md: Ptr[MatchData], mctx: Ptr[MatchContext]) -> Int32: ...

# Returns Ptr[UInt64] -- pointer into the match-data block holding the
# ovector (pairs of (start, end) byte offsets, length 2*group_count).
@native("::pcre2_get_ovector_pointer_8")
def get_ovector_pointer(md: Ptr[MatchData]) -> Ptr[readonly[UInt64]]: ...


# ---------- Pattern info ----------

# pcre2_pattern_info(code, what, where) -- writes the requested info into
# `where` (typed appropriately for the selector). We only use it to fetch
# the capture count (uint32_t).
@native("::pcre2_pattern_info_8")
def pattern_info_u32(code: Ptr[Code], what: UInt32, out: Ptr[UInt32]) -> Int32: ...


# ---------- Substitution ----------

# pcre2_substitute writes the result into a caller-provided UCHAR buffer.
# If the buffer is too small AND PCRE2_SUBSTITUTE_OVERFLOW_LENGTH is set,
# *outlen is updated to the size needed and PCRE2_ERROR_NOMEMORY returned;
# the caller can resize and retry. `mcache` is an optional match_data cache
# we don't use -- pass None.
@native("::pcre2_substitute_8")
def substitute(code: Ptr[Code],
               subject_data: Ptr[readonly[UInt8]], subject_len: UInt64,
               start_offset: UInt64, opts: UInt32,
               mcache: Ptr[MatchData],
               mctx: Ptr[MatchContext],
               repl_data: Ptr[readonly[UInt8]], repl_len: UInt64,
               outbuf: Ptr[UInt8], outlen_inout: Ptr[UInt64]) -> Int32: ...
