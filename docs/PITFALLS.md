# Pitfalls

Rules about the language and the generated code that keep being broken past a green suite and
a clean review. A class enters the list on its second hand catch; every entry here has several.
Each states the invariant, a real example -- the source shape, the wrong emit or output and the
right one, self-contained so it stays true after the fix -- and how to check it. A `BUGS.md`
slug trails an example only while that bug is open; the fix removes the pointer and leaves the
example.

`/tpy-fix-bug`, `/tpy-add-feature` and the review agents walk this list; the "Check" line is
what they do, not what they read.

---

## Semantics

### `silent-copy-vs-alias`

**Rule.** A reference type is never copied where CPython would alias. Where TPy semantics make
a copy unavoidable, the compiler warns and the user silences the warning with an explicit
`copy()`. A copy that neither warns nor is spelled is a defect.

**Example.** `V = Box(2)` and `G: tuple[Box, Box] = (V, V)` at module level, then `G[0].n = 42`.
Wrong: `extern std::tuple<Box, Box> G` (owning storage), so `V.n` prints 2 under TPy and 42 under
CPython, with no diagnostic. Right: the form the scalar global `G: Box = V` already takes, a
borrow slot `Box* G`, as the local tuple does with `std::tuple<Box*, Box*>`.
(open: `BUGS.md#global-tuple-ref-storage-form`,
`BUGS.md#callable-value-borrow-return-copies-unwarned`)

**Check.** Mutate the object after the boundary (return, yield, param, field store, container
insert, global) and print a field that shows whether the mutation reached the original, under
TPy and CPython. Read-only output is parity-blind. Then read the emitted C++ at the boundary:
a copy constructor, or a by-value slot where the scalar form is a pointer, is the defect.

### `copy-warning-at-wrong-site`

**Rule.** A "copies X" warning fires only where the emitted C++ actually copies, and every
actual copy of a reference type has a warning or an explicit `copy()`. Both directions.

**Example.** `s[0] = z` where `s` is a user class with `def __setitem__(self, index: int32,
value: Data)`. The emit is `::tpy::__setitem__(s, 0, z)`, a by-reference pass, yet the line warns
"copies Data into container; use copy()"; the copy, if any, is the callee's own `self.a = value`,
which warns on its own line. Inverse: `self.u = v` in a constructor with `v: Own[A | B]` warns the
same way although the member-init emits `u(std::move(v))`, so a `copy()` written to silence it
would add the copy the warning claims.
(open: `BUGS.md#setitem-copy-warning-at-call-site`, `BUGS.md#own-union-field-store-copy-warning`)

**Check.** For every line under a copy warning, open the emitted C++ at that line and find the
copy construction. A const-ref bind or a `std::move` under a copy warning is the defect; a
`copy()` whose removal would only change the warning is the defect too.

### `tuple-equals-scalar`

**Rule.** A one-element tuple has exactly the semantics of the scalar it wraps (aliasing,
copying, ownership, storage form, view-ness), and an N-tuple has, element-wise, the semantics
each element would have alone. Changing a return type from `Obj` to `(Obj, int)` must not
change how `Obj` behaves.

**Example.** `def f(x: Own[Box])` renders `Box&&` and the body may move `x`; `def f(p:
tuple[Own[Box], Box])` renders `const std::tuple<Box, const Box*>&`, so `owned, borrowed = p`
emits `auto __tup_1 = p;`, a whole-tuple copy, and `owned.n = 99; borrowed.n = 77; return
p[0].n + p[1].n` prints 78 under TPy and 176 under CPython: element 1 aliases, element 0 does
not. Same axis, other shapes: `xs.append(v)` at `list[Box]` warns and copies, while at
`list[tuple[Box, Box]]` the literal `xs.append((v, v))` is a hard error, a local `xs.append(t)` a
warning and a call result `xs.append(make(v))` silent; `a: str = v` is `std::string_view a = v`
while `t: tuple[str] = (v,)` is `std::tuple<std::string>(std::string(v))`.
(open: `BUGS.md#consume-own-element-of-mixed-tuple`, `BUGS.md#borrowed-tuple-at-own-call-arg`,
`BUGS.md#str-tuple-element-local-owned`)

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

**Example.** `return h.get()` at an `Own[Obj]` return, where `get` returns a borrow. Wrong: a
hard error at the return slot while the same call at an `Own` parameter, a container insert or a
field store only warned and copied; the return position carried a verdict of its own. Right:
one verdict at every owning slot, the warning "copies Obj into owned storage; use copy()" and
the copy, decided where the slot's ownership is decided and consumed by each position.

**Check.** For the construct under change, compile the same subject at two positions other
than the one the reporter saw and diff the emitted C++ for the subject. A difference is the
defect. A fix that touches several consuming sites instead of the deciding site is the same
defect in the compiler.

### `conditional-operand-evaluates-in-place`

**Rule.** An operand of a conditionally evaluated position -- the right side of `or` / `and`, a
ternary arm, a comprehension filter or element, an `assert` message, the later links of a
chained comparison -- is evaluated exactly when CPython evaluates it, and exactly once. Nothing
of it (a temp, an owned copy, a call, a property read) materializes before its guard has run,
and no operand is spelled twice in the emit to serve it.

**Example.** `(xs.pop() > 0) or take(xs)` with `take(xs: Own[list[int32]])`. Wrong: `auto
__tmp_1 = xs;` declared above the `||`, so the call sees the list before the pop where CPython
sees it after, and the copy is paid when the right side never runs. Right: the temp is created
inside the right operand's own region, after the guard. Earlier hits of the same class: a
container literal and a comprehension as the right operand of `or`, hoisted above the guard the
same way; a chained comparison whose middle operand is a `@property` read, with the getter
spelled twice so it ran twice and out of order.

**Check.** Put a side effect in the guard that the operand can observe (a `pop`, a counter, a
print) and run under both interpreters. Then read the emitted C++ of the statement for a
`__tmp` declaration, a copy or a call above the `||`, `&&`, `?:` or filter it belongs to, and
for an operand expression that appears twice.

### `generic-equals-monomorphic-twin`

**Rule.** A generic body instantiated at `T = X` has exactly the semantics, diagnostics and
emitted form of the same body written with `X` spelled directly. The type parameter is a
placeholder, not a further value shape: a form or ownership verdict taken while `T` is still
unresolved and not retaken at the instantiation is where the twin drifts.

**Example.** `def find[T](xs: list[T], v: T)` called as `find(names, k)` with `k: str` USED to
spell the slot `const std::string&` and buy the caller an owned copy, because `str` and `String`
shared `std::string` and a trait keyed on the C++ type could not tell their parameter forms
apart. Each of the four str/bytes types owns a C++ type now, so `param_val_or_ref_t<T>` at that
slot IS the twin's `std::string_view` -- the fix was to make the two spellings distinct, not to
carry the form beside `T`. The three slots that render CONST (a `readonly[T]` param, a
`@readonly` method's `T`, a constructor's) went with it: they spell `readonly_form_t<T>`, the same
parameter form const-qualified only where it is a mutable reference, because `const T&` binds
neither a view NOR anything a generic BODY could materialize -- there `T` is not fixed yet.
Same class, still open: a `T | None` RETURN is committed to `T*` for every instantiation, so
returning the body's own `T | None` parameter does not compile at a value `T` where the twin's
does; an open-`T` ORDERING compare has no form-neutral render, so it fails to compile at
`T = bytes`.

**One ACCEPTED divergence, decided deliberately** -- the owning-slot copy contract
(`tpyc/sema/own_copy.py`). The twin names the type it copies, once per concrete type; the generic
warns once at its own line in the hedged form (`may copy T into owned storage if not a value
type`), including where the program only ever instantiates it at value types, and does not name
the type at all. That is on purpose: a library author has no instantiation to consult and copyable
is TPy's default, so the declaration is the only place the contract can be stated. `copy()` and a
`T: ValueType` bound silence it; a non-copyable instantiation (`@nocopy`, or a record with
`__del__`) promotes the same line to the twin's error. Do not "fix" this drift back toward
the twin -- see `docs/LANGUAGE_FEATURES.md`, "the copy contract of a generic body".
(open: `BUGS.md#generic-own-slot-borrow-call-unwarned`,
`BUGS.md#generic-optional-return-committed-to-pointer`,
`BUGS.md#simple-generator-captures-open-t-param-by-reference`)

**Check.** For a changed rule that a generic body can reach, write the monomorphic twin at the
instantiation the case uses and diff the emitted C++ for the subject and the diagnostics; a
different form, verdict or warning is the defect, and a temporary, copy or allocation the twin
does not pay is a different form. Where the corpus spells only the generic,
the twin is a probe.

## Generated code

### `view-not-copy`

**Rule.** `str` and `bytes` are value types; the view-vs-owned distinction is an optimization,
not a semantics. A borrowed use takes a view (`std::string_view`, `::tpy::BytesView`);
materializing `std::string` or a byte copy where a view would do is a defect. `Own[str]` and
`Own[bytes]` transfer nothing, and at a parameter they select the owned form (`std::string` by
value) over the view, so they are idiomatic only where the callee must own the buffer (a field
store), and a case that uses them says why.

**Example.** `def take(s: Own[str])` in a new case, called with a view. Wrong: a `std::string`
built from the argument at the call, a copy the plain `str` parameter would not make. Right:
`def take(s: str)`, a `std::string_view`, unless the callee stores the buffer in a field.

**Check.** Grep for `Own[` on a value type, and for `std::string(`, `std::string ` and
`::tpy::Bytes(` in the emitted C++ of the shape under change; each needs a position where the
type mapping requires owned storage.

### `hidden-allocation`

**Rule.** The emitted C++ heap-allocates only where the TPy type mapping requires owned
storage: a `list`, `dict`, `set` or `bytearray`; a `String` or owned `bytes` that must hold its
own buffer; a `BigInt` outside the small-int range; `Box`, `Rc`, `Arc`; an asyncio `Task`'s
boxed coroutine; a `@dynamic` protocol adapter; a thrown exception.
Everything else in the mapping is inline or borrowed and must stay that way: fixed-width
ints, `char`, `bool`, tuples, records held in fields and containers, `str` and `bytes` at
borrowed positions (views), `Span`, `Ptr`. A hidden allocation is one the mapping does not
call for: a `std::string` built from a view, a container copied to pass or return, a `BigInt`
temporary for a literal or comparison that fits a fixed width, a temporary container built
only to iterate or convert, owned storage in a tuple or optional slot where the scalar slot
would be a view or a pointer.

**Example.** `a: str = v` emits `std::string_view a = v` (free), but `t: tuple[str] = (v,)`
emits `std::tuple<std::string>(std::string(v))`, an allocation plus a character copy per
element, twice for `tuple[str, str]`; `bytes` pays `::tpy::Bytes(v)` per element the same way.
`a < b` on two small `int` values must compare inline payloads without building limb
vectors; floor division, remainder, `divmod`, shifts and bitwise operations also
stay allocation-free when their small inputs produce a small result.
`k in names` with `k: str` and
`names: set[str]` built a `std::string` from the view to hash it, where the container looks up
the view itself.
(open: `BUGS.md#str-tuple-element-local-owned`)

**Check.** Read the emitted C++ of the shape under change for `std::string(`, `::tpy::Bytes(`,
`std::vector<...>(`, `BigInt(` temporaries, `from_str`, `make_`, `new ` and `__tmp` locals;
each must be one the type mapping requires at that position. Where the corpus
does not reach a position, compile a probe and read its emit. For runtime operators,
also inspect the callee: unchanged operator syntax can hide temporary allocations.

### `const-source-const-loop-var`

**Rule.** A loop variable that aliases its source -- the pointer form, `&(*it)` into a `T*` --
inherits the source's const-ness. A const-rooted source (a `readonly[...]` container, a `self`
field under a const-inferred receiver) or a `readonly[T]` element (what sema's auto-readonly flip
makes the DEFAULT for a `*args` pack the body does not mutate) gives `const T*`. The two wrong
answers cost differently: a mutable `T*` is a hard C++ error, and falling back to an owning slot
compiles by copying the element every iteration where every other path aliases -- the
`silent-copy-vs-alias` defect in a second costume, in a cell that is the norm rather than a
corner.

**Example.** `def walk(xs: readonly[list[Node]]) -> Iterator[int32]: for n in xs: yield n.v;
yield n.v` -- two yields, so the body is a resumable frame rather than the simple-generator
peephole. Wrong: the frame field `Node* n` against an advance that yields `const Node*` (g++
`invalid conversion from 'const Node*' to 'Node*'`); equally wrong, `::tpy::frame_slot<Node> n`,
which compiles and copies. Right: `const Node* n = nullptr;`. Same cell from the other two roots:
`def each(self) -> Iterator[int32]: for p in self.items:` with a const-inferred receiver, and
`def sizes(*xs: list[int32])` whose loop over the pack suspends.

**Check.** For an iteration whose source is `readonly`, a `self` field under a const receiver, or
an unmutated `*args` pack, read the loop var's frame field in the emitted C++: an owning
`frame_slot<T>` or a non-const `T*` is the defect. Then mutate the SOURCE between two pulls and
read it back through the next one under both interpreters -- a const alias shows the change, a
copy shows the stale value, and a read-only body cannot tell them apart.

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
tokens; compile the remedy every new message names. `tests/test_diag_text.py` runs the
grep over every committed `diag.txt`.

### `no-internal-names-in-diagnostics`

**Rule.** No compiler-internal identifier reaches the user: AST node class names, sema helper
names, THIR tags, reject-reason codes. Name the Python construct.

**Example.** `main.py:14: error: Unsupported sub-pattern in field binding: TpyOrPattern`
(`tpyc/sema/match.py` interpolated `type(node).__name__`; the line the diagnostic points at
already shows the user what they wrote, so the site now names nothing).

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

**Example.** Three `match` rejections landed in one branch as if they were rules, and CPython runs
all three: `case (Dog() | Cat()) as y:` over `Dog | Cat | Bird` binds `y` as the whole subject
and rejects `y.n`; `case A() | None:` over `A | B | None` errors "unsupported alternative in an
or-pattern over a union subject"; `case int32():` on `type Tree[T] = T | list[Tree[T]]` at
`Tree[int32 | str]` errors "'int32' is not a member of union". Each is now a filed gap.
(open: `BUGS.md#or-pattern-as-binding-no-join`, `BUGS.md#or-pattern-none-alt-union`,
`BUGS.md#recursive-alias-leaf-union-member-unnameable`)

**Check.** Run the rejected program under CPython (`PYTHONPATH=lib/cpy uv run python main.py`). If
it runs clean, find the slug or the documented rule; if neither exists, the rejection is the
defect.

### `no-warning-on-valid-code`

**Rule.** A warning fires only where the program has the property the warning names. A
warning on valid code is a defect; a comment explaining such a warning as expected is the
defect with a cover story.

**Example.** `case Dog() | None | _:` over `Dog | Fox | None` warns "non-exhaustive match on
'None | Dog | Fox'; missing: Fox" although an or-group containing a wildcard is a catch-all; the
case comment narrated the warning as the rule.
(open: `BUGS.md#match-exhaustiveness-or-wildcard`)

**Check.** For each warning in the diagnostics of a valid program, decide from the language
definition whether the named property holds; the surrounding comment is not evidence.
