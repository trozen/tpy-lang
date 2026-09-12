# String Width and Unicode Representation Design

Design decision record. Captures the resolved model for making `str` a
Python-code-point-semantic Unicode type whose internal character width is a
build-time choice, so the same language serves both byte-level C++ interop (ATS)
and full-Unicode CPython extensions. Implementation is a future
`/tpy-add-feature` pass (see "Build scope" below); this document is the design,
not yet code. It incorporates a critical review pass (Codex, 2026-07-01) and a
readiness retrospective (2026-07-01).

**Maintenance:** nothing in the codebase enforces this doc, so it can bit-rot as
`str`/`String`/sema churn underneath it. Re-validate it against the current code
at the START of the implementation `/tpy-add-feature` pass (it is the spec that
pass consumes); treat a divergence found then as a doc bug to fix, not a silent
design change.

## Status

| Aspect | State |
|--------|-------|
| `str` = Python code-point semantics within the width's representable range | Decided |
| Single build knob "string width" in {1, 2, 4, PEP-393}, default 4 | Decided |
| `char` = a representable code point, type derived from the width (not a separate knob) | Decided |
| Width is a whole-artifact ABI property | Decided |
| Blessed default (4) + opt-out; precompiled libs require width match | Decided |
| Width-agnostic library contract (sema-enforced core + lint/test obligations) | Decided |
| `String`/`DynStr` = owned `str`, width-tracking (not `std::string`-pinned) | Decided |
| Methods: ASCII+Latin-1 first pass, full UCD tables as a named follow-up | Decided |
| Build widths 1 (ATS) and 4 (extensions) | Not started -- first impl |
| Width 2 (UCS-2) and PEP-393 dynamic | Deferred -- documented future modes |
| Current `str` (`std::string`, byte-indexed, `char`=`char`, ASCII-only methods) | Shipped; superseded by this design |

## Motivation

Two real, near-term use cases pull `str` in different directions:

- **ATS low-level native code** integrating with C++ that uses single-byte
  `std::string`/`string_view` (Latin-1). Wants zero-copy interop and O(1)
  indexing; its string data is ASCII/Latin-1 by construction.
- **CPython extensions** that must round-trip arbitrary Python `str`
  (`PyUnicode`), including non-ASCII text and emoji. Wants full Unicode
  fidelity.

Today TPy's `str` is effectively an 8-bit string: `std::string` storage,
`len(s)` = byte count, `s[i]` -> `char` = `char` (one byte), and the
case/`is*` methods are ASCII-only. This matches CPython only for ASCII and
silently diverges on non-ASCII. The design below keeps one code-point-semantic
`str` (Python-correct within each width's representable range) while letting the
build pick the character width.

## Core decision: one "string width" knob

There is exactly ONE build setting -- the **string width** -- chosen from
{1, 2, 4, PEP-393}, default 4. It drives `str` storage, the `char` type, the
C++ interop container, and the representable code-point range together:

| Width | `str` storage | `char` | Range | Indexing | Zero-copy C++ `std::string` interop |
|-------|---------------|--------|-------|----------|-------------------------------------|
| 1 | `std::string` (Latin-1, 1 byte/char) | `char` | U+0000..U+00FF | O(1) | Yes (length-aware Latin-1 only) |
| 2 | `std::u16string` (UCS-2) | `char16_t` | U+0000..U+FFFF (BMP) | O(1) | No (convert) |
| 4 | `std::u32string` (UCS-4) | `char32_t` | U+0000..U+10FFFF | O(1) | No (convert) |
| PEP-393 | per-string tagged 1/2/4 | `char32_t` | full | O(1) | No (convert) |

`char` is **derived** from the width, never an independent option: `char` must
hold any code point `str` can produce, so a narrower `char` than the width
would truncate (unsound) and a wider one is redundant. A 1-byte build gets a
compact `char` `char` that also matches the C++ side's `char`; a 4-byte build
gets `char32_t`.

Precisely, `char` is **one representable code point for the active width**, not
"any Unicode code point." A `chr(n)`, character literal, or `str` index that
would yield a code point outside the width is a width-overflow (see
"Representable range and overflow"). Note the type asymmetry with Python:
Python's `s[i]` yields a one-character `str`, whereas TPy's `char` is a distinct
value type bounded by the build width.

`str` storage is **not UTF-8**, and that is a forced move, not a preference: the
load-bearing requirement is Python's O(1) `s[i]` / `len` on code points. UTF-8 is
variable-width, so code-point indexing over it is O(n) (and a naive
`for i in range(len(s)): s[i]` loop O(n^2)) -- Rust and Swift accept this by
dropping integer code-point indexing, but TPy must keep Python's `s[i]`
semantics. Fixed-width code units (1/2/4 byte arrays) make every code point one
storage unit, so `s[i]` is a plain index -- O(1). UTF-8 remains an I/O / boundary
encoding only, exactly as in CPython (fixed-width internally, UTF-8 at I/O).

## Semantics: Python code points, within the representable range

Regardless of width, `str` observes Python semantics **for every code point the
width can represent**: `len(s)` is the code-point count, `s[i]`/iteration/slicing
operate on code points, `char` is a code-point value. Width changes the
*representation* (storage size, representable range, interop container), never
the observable behavior within its range.

This is NOT full CPython parity for widths 1 and 2: CPython's `str` accepts every
Unicode code point, so rejecting an emoji at width 1/2 is an observable language
difference, not merely a representation detail. Full-Unicode parity is a width-4
(or PEP-393) property; narrower widths trade representable range for compactness
and interop, and are Python-correct only on their subset.

Latin-1 identity (width 1): the code-point *values* of a width-1 build match
CPython's compact Latin-1 representation for U+0000..U+00FF -- byte index equals
code-point index across 0..255. This is code-point-value equivalence, not object
identity: CPython's `str` object also carries kind flags, a cached UTF-8 form,
and interning that a `std::string` does not. The ATS "byte semantics is fine"
intuition holds precisely because Latin-1 storage makes byte == code point on
that subset.

### Representable range and overflow

A code point outside the build width (any non-Latin-1 at width 1; astral-plane at
width 2) cannot be stored, so every string-producing operation is a potential
overflow site. The design goal is **fail-loud, never silent-wrong**; the *timing*
of the diagnostic depends on whether the code point is statically known:

| Producing operation | Overflow handling |
|---------------------|-------------------|
| String literal / char literal | **Compile-time error** (code points known at parse) |
| `chr(n)` with constant `n` | **Compile-time error** |
| `chr(n)` with runtime `n` | **Runtime** exception |
| Decoding `bytes` (`.decode()`) | **Runtime** (input-dependent) |
| CPython boundary marshalling (`PyUnicode` in) | **Runtime** boundary error |
| `@native` returning a `str` | **Runtime** (validated at the boundary) |
| Concat / slice of in-range inputs | Cannot introduce a new out-of-range code point (no check) |
| Case mapping (`upper`/`lower`/...) | **Runtime** IF a mapping yields a wider code point |

(Exact exception types/messages are an implementation detail to settle in the
feature pass; the invariant is that none of these is a silent wrong answer.)

### Source literals

TPy consumes the code points the Python parser already decoded from the source
(honoring source encoding declarations, escapes, raw/adjacent literals) -- it
does not re-implement UTF-8 source decoding. Each literal's code points are then
validated against the build width: U+00A3 (pound sign) fits Latin-1 and is
accepted at every width; an emoji literal (astral plane) is a compile-time error
at widths 1 and 2 and accepted at width 4.

## The width-agnostic contract

For libraries (the stdlib included) to compile at *any* width -- which is what
makes the opt-out distribution model below possible -- all portable code must be
written width-agnostic. The contract is essentially "behave like Python `str`":

- `char` is an **opaque code-point value**. It converts to/from `int` only via
  `ord`/`chr` (Unicode scalar values, width-independent) and compares against
  character literals. No raw byte arithmetic on `char`, no reinterpreting its
  storage as bytes.
- `str` exposes **no raw fixed-width buffer** to portable code. Index, iterate,
  slice, and the string methods are the only sanctioned access. Raw-byte work
  goes through `bytes` / `.encode()` / `.decode()`.

This is Python's own model (Python has no raw-buffer access to `str`; you
`.encode()`). The only things today's TPy does that violate it are `char` =
`char` and `str.data()`-style C++ interop.

Enforcement is layered -- sema is necessary but not sufficient:

- **Sema-enforced (mechanical):** no raw byte arithmetic on `char`, no
  reinterpreting `char`/`str` storage as bytes, no raw fixed-width buffer access,
  and width-gated native signatures (a `@native` grabbing a raw `str` buffer in a
  width != 1 build is a compile error). These are the violations that compile at
  width 1 but break at width 4, so they must be caught at the source.
- **Lintable convention (advisory):** width assumptions sema cannot see -- an
  algorithm that assumes `ord(c) < 128`, hardcodes ASCII ranges, or is otherwise
  only correct on a subset. A lint can flag common shapes; it cannot prove
  semantic width-agnosticism in general.
- **Test obligation:** a library claiming width-agnosticism must be exercised at
  more than one width (the CI matrix), since neither sema nor lint fully proves
  it.

**Open risk (most likely to be wrong on contact with code):** the mechanical
sema layer is a best-effort gate, not a proof. If width assumptions leak past it
in practice, the fallback is unresolved -- widen the sema rules, escalate the
lint to an error for the leaking shape, or accept it as a documented
test-caught-only class. Decide the fallback when the enforcement meets real code.

The one inherently width-aware escape is C++ interop that wants `str` as
`const char*`: it is gated to width 1 (where `str` = `std::string`), otherwise
the boundary must go through an explicit `.encode()` to `bytes`.

## ABI and granularity: whole-artifact

The width is a property of the final build artifact (binary or extension
module): every module linked into it shares one width. The width *is* the `str`
ABI -- `str` fields, `list[str]`, and struct layouts all differ by width, so two
modules with different widths cannot share strings (same rule as not mixing
`std::string` ABIs). A precompiled TPy library cannot be a single object usable
across widths; it is compiled per width. This is natural because tpyc compiles
modules from source each build.

Because width changes generated output, it must be a first-class input to every
artifact-identity mechanism, not just the type layer. Checklist for the feature
pass:

- **Exec / stdlib-`.o` / PCH cache keys** must include the width (a width change
  re-keys every case, like a toolchain change).
- **Generated artifact paths / extension-module tags** must be width-tagged so
  two widths do not collide or silently reuse each other's output.
- **Snapshot baselines** (`expected/` `.hpp`/`.cpp`) are width-specific; the test
  suite pins a width (see "cpy-parity" below).
- **Precompiled-library metadata** records its width; linking a mismatched width
  is a hard error, not a silent ODR/ABI hazard.
- Name mangling / template instantiations of `str`-bearing types differ by width
  and must not be assumed stable across widths.

## Distribution: blessed default + opt-out

- The **blessed default is width 4** (full Unicode). Rationale: correctness by
  default -- width 4 represents every code point, round-trips any `PyUnicode`
  losslessly, and matches CPython, so the default never silently rejects valid
  text; apps opt *down* to a narrower width for a known-subset hot path. This is
  a real call, not neutral: 4 bytes/char is 4x the memory of width 1 on
  ASCII/Latin-1 data -- in tension with performance (goal #1) -- and it is
  ABI-locked, so it is the choice most likely to be revisited once real workloads
  exist. It is chosen deliberately (safe-by-default over compact-by-default) with
  opt-out as the pressure valve. Most apps use the default, so the precompiled
  stdlib / third-party set is built once at width 4 and shared.
- Apps with special needs **opt out** (ATS sets width 1). Opting out means the
  app's whole dependency set -- stdlib included -- is recompiled from source at
  the chosen width; it cannot reuse the width-4 precompiled set.
- Opt-out therefore *depends on* every shipped library being width-agnostic in
  source (the contract above). This is what makes the contract load-bearing, not
  cosmetic.

## Interop consequences (accepted)

- **Zero-copy `std::string`/`string_view` interop exists only at width 1, and
  only for length-aware Latin-1 APIs.** At widths 2/4 the C++ container is
  `u16string`/`u32string`, so a full-Unicode native app talking to C++
  `std::string` code pays a conversion. Even at width 1 the zero-copy story holds
  only for C++ that treats the buffer as length-delimited Latin-1 bytes: a
  `const char*` API assuming UTF-8, a locale encoding, or NUL-termination is
  unsafe (a width-1 `str` may contain embedded NUL and is not NUL-guaranteed) and
  must go through explicit encoding. ATS's single-byte `std::string` /
  `string_view` usage is exactly the length-aware Latin-1 case.
- **CPython extensions:** width 4 round-trips any `PyUnicode` losslessly; the
  existing `str` boundary marshalling (UTF-8 based) decodes `PyUnicode` to code
  points and encodes to the build width. Width 1 would accept only Latin-1
  Python strings (else a boundary error) -- which is why Unicode extensions use
  width 4.

## Build scope

Implement the two widths there is real demand for now:

- **Width 1** -- ATS low-level native code.
- **Width 4** -- full-Unicode CPython extensions.

Defer, as documented future modes behind the same `str` interface (no user-code
change to add later):

- **Width 2 (UCS-2)** -- only if a BMP-only, memory-sensitive workload appears.
- **PEP-393 dynamic** -- a *second* string implementation (runtime kind tag,
  branchy `s[i]` decode), roughly doubling the string runtime surface. It is
  **source-compatible** with the fixed-width modes (same `str` interface, no
  user-code change to add it) but **ABI- and performance-distinct**: its runtime
  layout, `char`-extraction path, native ABI, and cache keys differ, so it is its
  own build target with its own test matrix, not a drop-in behind the existing
  machinery. Add only when one app must be full-range AND compact AND O(1) at
  once; a width-4 build already gives full-range + O(1) at 4x memory, covering
  most of that need. `char` is already `char32_t` in this mode, so no `char`
  change is needed to add it.

The contract (`char` = code point, opaque `str`) and the width machinery should
land **together** in the first pass: the migration cost is changing `char` from
`char` to a code-point type, worth paying once, when there is payoff.

## Migration from today

Changes from the current model (`docs/STRING_HANDLING.md`):

- `char` changes from `char` to the width-derived code-point type (`char` at
  width 1, `char32_t` at width 4). Touches every `s[i]`, char literal, `char`
  field, and the `char`/`bytes` boundary.
- `s[i]` / `len` / iteration become code-point operations (a no-op for ASCII;
  correct for Latin-1 at width 1; correct for all of Unicode at width 4).
- String methods become code-point-aware -- but this is the largest hidden work
  item, not a mechanical widening. Python's `upper`/`lower`/`is*` are driven by
  the Unicode character database and are sometimes **length-changing** (German
  sharp-s upper-cases to `"SS"`), so a `char -> char` mapping is insufficient;
  case conversion returns a `str`, not a `char`. **Scoped (see "Method fidelity"
  below):** the first pass ships ASCII + Latin-1-correct methods (a small fixed
  256-entry table, which closes the BUGS.md Latin-1 case gap at width 1); full
  UCD tables are a named follow-up. Even the Latin-1 table is not trivial -- its
  length-changing and escapes-Latin-1 cases apply at width 1.
- `str` storage type becomes width-parameterized (`std::string` /
  `std::u32string` / ...). The context-dependent `str` rules (param =
  view, return/field = owned) carry over per width.
- The cpy-parity test corpus runs under CPython (full Unicode), so it validates
  the **width-4** build directly. Widths 1/2 need width-specific test modes,
  since CPython accepts code points they reject:
  - width 4: full CPython parity.
  - width 1/2: representable-subset parity (ASCII/Latin-1 or BMP fixtures) plus
    **explicit negative tests** that out-of-range construction fails loudly.
  Tests exercising a width's range limit use `no_cpython.txt` (CPython would not
  diverge -- it just succeeds where a narrow build errors).

## Decided (grilling follow-up, 2026-07-01)

- **`String`/`DynStr` tracks the build width** -- it is the always-*owned* form
  of `str` (`std::string`@1, `u16string`@2, `u32string`@4), keeping its current
  role (force owned even at param position: `const <container>&` instead of a
  view). It is NOT pinned to `std::string`, so the single-knob model has no
  exception. The "hand C++ a real `std::string`" need is a `@native`-boundary
  concern (encode `str` -> `bytes`, marshal to `std::string` there), not a pinned
  user type. (Retires the earlier "pin `String` to `std::string`" idea.)
- **Method fidelity: ASCII + Latin-1 now, full UCD as a named follow-up.** The
  first pass ships correct case/`is*` for ASCII and Latin-1 via a small fixed
  256-entry table -- closing the BUGS.md Latin-1 case gap at width 1 (the ATS
  case) and the common path. Full Unicode-database case/property tables (astral
  planes, full case folding, all scripts), generated from the UCD (the source
  CPython's `unicodedata` uses), are a **bounded follow-up milestone** and a
  prerequisite for claiming width-4 CPython *method* parity. The width machinery
  (representation, `char`, O(1) index/`len`/iteration/slicing) does not depend on
  it and ships first.
  - **Coherence caveat (default vs first-complete target):** the blessed default
    (width 4) is exactly the width whose first pass ships *incomplete* methods
    (correct indexing over the full range, but case/`is*` only ASCII+Latin-1). The
    only width whose FIRST pass is fully complete is width 1 -- there the
    256-entry table IS full fidelity (modulo the escapes-Latin-1 handful below).
    So the honest first-shippable *complete* target is width 1 (ATS); width 4 is
    range-complete but method-incomplete until the UCD follow-up lands.
  - Latin-1 is not fully self-contained even so: **length-changing** case (result
    in ASCII, representable) must be handled at width 1; and a few chars
    upper-case *out* of Latin-1 -- U+00FF -> U+0178, U+00B5 -> U+039C -- whose
    results are unrepresentable at width 1, so they are width-overflow (per the
    overflow table). Open sub-decision: error vs a documented no-op for that
    ~3-char handful (CPython produces the wider code point).

## Open items

- **Overflow exception surface.** The exact exception types and messages for
  runtime width-overflow (`chr`, `.decode()`, boundary marshalling, native
  returns, and the width-1 escapes-Latin-1 case above) per the overflow table.
- Exact width-selection surface (CLI flag / project setting / module pragma) and
  how it composes with the existing compile options.
- Grapheme-cluster correctness is explicitly out of scope (code points only,
  matching Python `str`; ICU-style grapheme handling is a separate concern).

## Relationship to existing docs

- `docs/STRING_HANDLING.md` -- current `str`/`StrView`/`String` model this
  design supersedes for the representation/`char` axis.
- `docs/OWNERSHIP_DESIGN.md` -- `str` as an immutable value type (copy-on-store,
  move-when-dead); unchanged by this design.
- CPython interop (`docs/CPYTHON_INTEROP.md`) -- the `str` boundary marshalling
  this design feeds (decode `PyUnicode` -> code points -> build width).
