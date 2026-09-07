# Pitfalls

Rules about the language and the generated code that keep being broken past a green suite and
a clean review. A class enters the list on its second hand catch; every entry here has several.
Each states the invariant, a real example with its `BUGS.md` slug where one is filed, and how
to check it.

`/tpy-fix-bug`, `/tpy-add-feature` and the review agents walk this list; the "Check" line is
what they do, not what they read.

---

## Semantics

### `silent-copy-vs-alias`

**Rule.** A reference type is never copied where CPython would alias. Where TPy semantics make
a copy unavoidable, the compiler warns and the user silences the warning with an explicit
`copy()`. A copy that neither warns nor is spelled is a defect.

**Example.** A module global `g: tuple[Int32, Cell]` emitted as `extern std::tuple<int32_t, Cell> g`
(by value) while a scalar `Cell` global is a pointer: `g[1].v = 42` does not reach the original.
(`BUGS.md#global-tuple-ref-storage-form`)

**Check.** Mutate the object after the boundary (return, yield, param, field store, container
insert, global) and print a field that shows whether the mutation reached the original, under
TPy and CPython. Read-only output is parity-blind. Then read the emitted C++ at the boundary:
a copy constructor, or a by-value slot where the scalar form is a pointer, is the defect.

### `copy-warning-at-wrong-site`

**Rule.** A "copies X" warning fires only where the emitted C++ actually copies, and every
actual copy of a reference type has a warning or an explicit `copy()`. Both directions.

**Example.** "copies Data into container" reported at `s[0] = z` where `s` is a user class whose
`__setitem__` binds the value by const reference; nothing copies on that line. Inverse:
`self.u = copy(v)` for `v: Own[A | B]` written only to silence a warning although the emitted
member-init is already `u(std::move(v))`.
(`BUGS.md#setitem-copy-warning-at-call-site`, `BUGS.md#own-union-field-store-copy-warning`)

**Check.** For every line under a copy warning, open the emitted C++ at that line and find the
copy construction. A const-ref bind or a `std::move` under a copy warning is the defect; a
`copy()` whose removal would only change the warning is the defect too.

### `tuple-equals-scalar`

**Rule.** A one-element tuple has exactly the semantics of the scalar it wraps (aliasing,
copying, ownership, storage form, view-ness), and an N-tuple has, element-wise, the semantics
each element would have alone. Changing a return type from `Obj` to `(Obj, int)` must not
change how `Obj` behaves.

**Example.** From one census: the global-tuple storage form above; a borrowed tuple at an
`Own[T]` call-arg slot taking three different verdicts
(`BUGS.md#borrowed-tuple-at-own-call-arg`); a `str` element at a local materializing owned
storage where the scalar local keeps a view (`BUGS.md#str-tuple-element-local-owned`);
consuming the `Own` element of a mixed owned-plus-borrowed tuple param
(`BUGS.md#consume-own-element-of-mixed-tuple`).

**Check.** Wrap the subject in `(x,)` and `(x, 1)` and diff the three variants' emitted C++ for
the element at the same position: a different storage form, deref, view or move verdict is the
defect.

### `same-construct-every-position`

**Rule.** A construct has the same semantics and the same emitted shape wherever it can appear:
free function, method, constructor, module-level statement, generator body, async body,
comprehension, closure, context-manager body, `try`/`finally`, `@error_return` body, `match`
arm. Across the value shapes too -- scalar, tuple, `Optional`, union, `str`/`bytes`, `Own[T]`,
`readonly[T]`, `Ptr`/`Span`, `Box`/`Rc` -- and across the slot kinds: local, param, return,
field, container element, global. The compiler decides
each fact once and every position consumes that decision; a position with its own copy of the
logic is where the next divergence lives.

**Example.** An integer-literal fold that held at free-function and constructor argument
slots but not at a method-argument slot, so `x.shift(1 << 3)` kept a runtime shift the
sibling positions folded. (`BUGS.md#thir-int-methodarg-shift-not-folded`) A borrow-returning
call at an `Own[T]` return slot was a hard error while the same source at a param, a container
insert or a field store only warned; the return position had its own verdict (fixed
2026-09-07, every owning slot now warns and copies).

**Check.** For the construct under change, compile the same subject at two positions other
than the one the reporter saw and diff the emitted C++ for the subject. A difference is the
defect. A fix that touches several consuming sites instead of the deciding site is the same
defect in the compiler.

## Generated code

### `view-not-copy`

**Rule.** `str` and `bytes` are value types; the view-vs-owned distinction is an optimization,
not a semantics. A borrowed use takes a view (`std::string_view`, `std::span<const uint8_t>`);
materializing `std::string` or a byte copy where a view would do is a defect. `Own[str]` and
`Own[bytes]` transfer nothing, and at a parameter they select the owned form (`std::string` by
value) over the view, so they are idiomatic only where the callee must own the buffer (a field
store), and a case that uses them says why.

**Example.** `def take(s: Own[str])` in a new case, materializing a `std::string` copy of the
argument at the call.

**Check.** Grep for `Own[` on a value type, and for `std::string(`, `std::string ` and
`bytes_copy(` in the emitted C++ of the shape under change; each needs a position where the
type mapping requires owned storage.

### `hidden-allocation`

**Rule.** The emitted C++ heap-allocates only where the TPy type mapping requires owned
storage: a `list`, `dict`, `set` or `bytearray`; a `String` or owned `bytes` that must hold its
own buffer; a `BigInt` outside the small-int range; `Box`, `Rc`, `Arc`; an asyncio `Task`'s
boxed coroutine; a `@dynamic` protocol adapter; a thrown exception.
Everything else in the mapping is inline or borrowed and must stay that way: fixed-width
ints, `Char`, `bool`, tuples, records held in fields and containers, `str` and `bytes` at
borrowed positions (views), `Span`, `Ptr`. A hidden allocation is one the mapping does not
call for: a `std::string` built from a view, a container copied to pass or return, a `BigInt`
temporary for a literal or comparison that fits a fixed width, a temporary container built
only to iterate or convert, owned storage in a tuple or optional slot where the scalar slot
would be a view or a pointer.

**Example.** `a: str = v` emits `std::string_view a = v` (free), but `t: tuple[str] = (v,)`
emits `std::tuple<std::string>(std::string(v))` -- an allocation plus a character copy per
element, twice for `tuple[str, str]`; `bytes` pays `bytes_copy(v)` per element the same way.
Comparing two small tagged ints through `BigInt::compare()` performs two heap allocations
because the small-int fast path is missing. (`BUGS.md#str-tuple-element-local-owned`,
`BUGS.md#bigint-compare-small-int-alloc`)

**Check.** Read the emitted C++ of the shape under change for `std::string(`, `bytes_copy(`,
`std::vector<...>(`, `BigInt(` temporaries, `from_str`, `make_`, `new ` and `__tmp` locals;
each must be one the type mapping requires at that position. Where the corpus
does not reach a position, compile a probe and read its emit.

### `generated-cpp-readability`

**Rule.** Generated C++ is read by the developer and by reviewers. A member-init list with
more than one initializer, a call whose arguments do not fit one line, or a comprehension body
renders one item per line; a single emitted line should fit an editor width (about 100
columns).

**Example.** `inline Grid::Grid(int32_t n) : cells(...), tags(...), data(...), mirror(...) {}`
on one line.

**Check.** Read the changed snapshot for one-line multi-initializer lists and lines far past
100 columns.

## Diagnostics

### `no-cpp-in-diagnostics`

**Rule.** A diagnostic names Python constructs and TPy types only. The user sees Python, may
not read C++, and a second backend is planned: no `std::`, `::tpy::`, `int32_t`, `variant`,
`string_view`, pointer sigils or C++ header names in message text. A remedy the message names
(`use copy()`, `assign to a local first`) is a spelling that compiles as written: an `Own`
return over an awaited borrow told the user to copy, and `copy(await ...)` did not lower.

**Check.** Grep new diagnostic strings in `tpyc/` and the changed `diag.txt` files for those
tokens; compile the remedy every new message names. Baseline debt: the escaping-closure
capture error says `string_view` in two committed `diag.txt` files; reword it when that
diagnostic is next touched, and a lint starts from that allowlist.

### `no-internal-names-in-diagnostics`

**Rule.** No compiler-internal identifier reaches the user: AST node class names, sema helper
names, THIR tags, reject-reason codes. Name the Python construct.

**Example.** `main.py:14: error: Unsupported sub-pattern in field binding: TpyOrPattern`
(`tpyc/sema/match.py`, a `type(node).__name__` interpolation; the user needs "or-pattern").

**Check.** Grep the diff for `__name__`, `type(` and `Tpy[A-Z]` inside message strings, and
the changed `diag.txt` files for `Tpy[A-Z]\w+`.

### `reject-valid-python-only-as-documented-divergence`

**Rule.** TPy rejects a program CPython runs only as a documented divergence: either a filed
gap (`BUGS.md#<slug>`) or a stricter-by-design rule recorded in `docs/LANGUAGE_FEATURES.md`.
A rejection with neither is a defect, however the diagnostic is worded. This scopes to
rejections TPy CHOOSES -- a rule of the language; a THIR "this construct is not yet supported
by C++ code generation" reject is a different class (a shape not lowered yet), disclosed by
its own diagnostic text, pinned by the `error_` case that carries it (the case is the
tripwire: it fails the day the shape lowers), and queued for a lowering arm in
`scripts/thir_migration/review/`; it needs no slug.

**Example.** Three `match` rejections landed in one branch as if they were rules: nested `as`
over an or-group, `case A() | None:` on a union, a recursive-alias leaf pattern. CPython runs
all three; each is now a filed gap.
(`BUGS.md#or-pattern-as-binding-no-join`, `BUGS.md#or-pattern-none-alt-union`,
`BUGS.md#recursive-alias-leaf-union-member-unnameable`)

**Check.** Run the rejected program under CPython (`PYTHONPATH=lib/cpy uv run python main.py`). If
it runs clean, find the slug or the documented rule; if neither exists, the rejection is the
defect.

### `no-warning-on-valid-code`

**Rule.** A warning fires only where the program has the property the warning names. A
warning on valid code is a defect; a comment explaining such a warning as expected is the
defect with a cover story.

**Example.** `case Dog() | None | _:` over `Dog | Fox | None` warned non-exhaustive although an
or-group containing a wildcard is a catch-all; the case comment narrated the warning as the
rule.
(`BUGS.md#match-exhaustiveness-or-wildcard`)

**Check.** For each warning in the diagnostics of a valid program, decide from the language
definition whether the named property holds; the surrounding comment is not evidence.
