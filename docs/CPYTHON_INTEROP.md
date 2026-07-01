# CPython Interop -- Design

**Status: v1.0 in progress.** Free functions marshalling every scalar
(`int`/BigInt, `float`, `bool`, all fixed-width int types `Int8`..`Int64` /
`UInt8`..`UInt64`) plus void return (`-> None`) and `str`/`bytes` (copy-in;
owned-form marshalling, the borrow-form param converted at the call) are
implemented -- see "v1.0 resolved design" below and `tests/interop/`; the rest
of this doc is the agreed design ahead of implementation. Companion to `PROJECT_TOOLING_DESIGN.md`, which reserves the
*tooling* hooks (the `ext` target kind + TPy as a PEP 517 build backend);
this doc is about the *interop semantics* that doc deliberately defers.

This is distinct from `NATIVE_INTEROP.md` (the `@native` system, which
declares C++ functions/types visible to TPy). CPython interop is the
inverse and a separate concern: moving values across the boundary between
TPy's native representation and CPython's `PyObject*` world.

## Scope

Two directions, both wanted; **extension leads**, embedding follows
behind reserved hooks:

| Direction | What | Use case | Priority |
|---|---|---|---|
| **Extension** | CPython imports a TPy-built `.so` | speed up a hot path in an existing Python program (the Cython / PyO3 flow) | **v1** |
| **Embedding** | a TPy binary links libpython and calls Python | reuse an unported Python library during migration | reserved hooks only |

v1 deliverables (the user-facing goal):

1. Write CPython extensions in TPy.
2. Expose free functions.
3. Expose classes and methods.
4. Expose enums and constants.

Async (bridging TPy's resumable-frame model to `asyncio`) is **out of
scope for v1** but must not be locked out -- see "Reserved hooks."

## v1 plan and status

**v1.0 (first shippable MVP) = phases 1-3** -- write extensions, expose free
functions, and numeric buffer input (copy-in; zero-copy deferred to 3.5). **v1.1 = phases
4-5** -- classes + methods, enums + constants. Staging the release keeps the
heavier class machinery (`tp_new`/`tp_dealloc`, dunders, aliasing) off the
first-ship critical path (per the co-validate review); all four original
goals still land in v1. Phases 3.5 / 6 / 7 / 8 are **beyond v1** (later or
IR-gated) and do **not** block it. Step-by-step detail is in "Suggested
phasing" near the end; this table is the at-a-glance tracker.

Status legend: 🔬 designed (captured in this doc, not started) -> 🚧 in
progress -> ✅ done.

| Phase | Deliverable | Scope | Status |
|---|---|---|---|
| 1 | Marshalling layer + cpython facade (abi3) -- the keystone | **v1.0** | 🚧 all scalars (int/BigInt, float, bool, fixed-width ints) + void return + str/bytes (copy-in) + list/dict/set/tuple (copy-in, recursive) done |
| 2 | Extension codegen; **free functions** end-to-end; local `.so` build | **v1.0** | 🚧 every scalar arg/return + void return + str/bytes + container arg/return done; positional + keyword args (PyArg_ParseTupleAndKeywords) |
| 2.5 | PEP 517 backend -> abi3 wheel (packaging) | **v1.0** | 🔬 |
| 3 | Buffer input -- numeric (copy-in in v1.0; zero-copy -> 3.5) | **v1.0** | ✅ done: `Span[readonly[T]]`/`Span[T]` (fixed-width int/`float`) as an @export fn PARAM only, via `PyObject_GetBuffer`; copy-in for both forms (no write-back for either); a mutated `Span[T]` param warns (copy-in, not visible to caller) |
| 4 | **Classes + methods** (`PyType_FromSpec`; dunders per Q4) | **v1.1** | 🚧 baseline done: construct + plain methods + annotated fields as getset, instances as free-fn/method params (borrow) + returns (copy); dunders/inheritance/@property/class-typed fields deferred |
| 5 | **Enums + constants** | **v1.1** | 🚧 `@export` enums recreated as real CPython IntEnum/Enum (functional API, module= set); enum values cross as @export fn params/returns (member round-trip, strict-by-type IN); `Final` scalar/str constants as init-time module-attribute snapshots; nested/cross-module enums deferred |
| 3.5 | Foreign-borrow primitive -> zero-copy str + buffer input | post-v1.0 (next) -- IR-gated | 🔬 |
| 6 | Containers (`list`/`dict`/`set`/`tuple`, by-copy) | v1-adjacent | 🚧 done: list/dict/set/tuple cross as @export fn params/returns, O(n) recursive copy-in/out (str/bytes elements + arbitrary nesting); strict-by-container-kind IN; a mutated container param warns (copy-in, not visible to caller); exposed class/enum *elements* deferred |
| 7 | `nogil` / `with gil` (parallelism + GIL checking) | later | 🔬 |
| 8 | Embedding, callbacks / opaque `PyRef`, async <-> `asyncio` | later | 🔬 |

The keystone is **phase 1** (marshalling layer + facade); it unlocks
everything else and is the natural `/tpy-add-feature` entry point.

## v1.0 resolved design (grilling pass, 2026-06-24)

A design-interview pass over phases 1-3 resolved the open shape questions
and reframed the build as a single **vertical thread** (one function
imported from CPython, end to end) rather than a marshalling layer built in
a vacuum. Confirmed against the current `tpyc` sources: the THIR migration
is **active but isolated** -- it lives on a parallel feature branch
(`thir-increment-1`, off the 0.4.0 freeze, not on master), is **dual-mode /
off-by-default / eligibility-gated** (only a narrow value-scalar slice
routes through THIR; everything else stays on the AST path), and has not
merged to master. On master, codegen is still AST-driven and every surface
this work touches -- the `(hpp, cpp)` `generate()` contract +
`generate_fwd_header` sibling, `all_cpp_paths`, `ModuleDirectives` /
`_DIRECTIVE_SPECS`, `FunctionLinkage` / `RecordLinkage`, the string-keyed
`BorrowTracker` -- is stable there. v1.0 stays IR-independent by design
(copy-in, no foreign-borrow primitive). The interop compiler footprint (a
sibling `generate_extension_glue()` plus parser hooks) barely overlaps
THIR's body-lowering seam (an early return in `StatementGenerator.gen_body`
keyed by `id(func)`), so the real cost of the two parallel branches is
**branch coordination, not architectural conflict** -- whichever merges to
master first, the other reconciles mechanically, and interop just becomes
part of the corpus THIR's byte-identical net must preserve. (Post-v1.0,
interop's deferred zero-copy foreign-borrow converges with THIR's form
design -- the same long pole.)

### The slice-1 ladder (build in this order)

- **Rung 0 -- `def answer() -> Int64: return 42`** (`METH_NOARGS`). The
  thinnest import: exercises `ext_module`, `@export`, the glue TU,
  `PyMethodDef` / `PyModuleDef` / `PyInit_`, the `.so` build, import, and
  `to_py(int64) -> PyLong`, with **zero argument marshalling**.
- **Rung 1 -- `def add(a: Int64, b: Int64) -> Int64`** (`METH_VARARGS`).
  Adds `from_py<int64>` (`PyLong_AsLongLong` + overflow -> `OverflowError`).
- **Rung 2 -- `int` / BigInt marshalling**, on its own (see below). TPy
  `int` is BigInt, whose `PyLong <-> BigInt` path is the *hardest*, not the
  easiest -- so it is deliberately last, not the lead.

### Locked decisions

- **First milestone is the vertical thread, not the marshalling layer
  alone** -- a `to_py`/`from_py` layer with no caller cannot be validated;
  the cheapest way to be wrong about a C-API detail is to not run it.
- **Packaging (PEP 517 / wheel) is a separate, later milestone** (the
  existing phase 2.5) -- disjoint failure surface from interop correctness;
  the test harness builds the `.so` via `tpyc` directly, so the verification
  path never goes through packaging anyway.
- **Trigger: the `# tpy: ext_module` directive** (a 3-line `_DIRECTIVE_SPECS`
  addition), not a CLI flag -- "this module is a CPython extension" is a
  code-coupled fact. Naming is filename-derived: `foo.py -> PyInit_foo ->
  foo.so`.
- **Exposure: bare `@export`, anchored by `ext_module`.** `@export` means
  "this module's public *foreign* surface"; the module kind picks the ABI
  (normal -> `extern "C"`; `ext_module` -> generate the Python wrapper).
  Conflicting forms inside an `ext_module` (e.g. `binding="C"`) are **loud
  compile errors**, so the reinterpretation can never silently miscompile.
  Recorded as an `exposed_to_host` marker **on the function AST node**
  (linkage stays `DEFAULT`; the function is a normal TPy function, the
  wrapper is separate) -- the IR-aligned placement that lowers to a THIR
  field. No new `RecordLinkage.EXPORT` until classes (phase 4).
- **Marshalling is three layers**, not one: (1) the **generated
  per-function wrapper** (the glue TU); (2) **hand-written C++ template
  per-type marshallers** `tpy::interop::from_py<T>` / `to_py` in the runtime
  facade, selected by codegen emitting the C++ type name it already knows
  (no per-type `if`-chain in codegen); (3) the **`@native`
  `lib/tpy/_bindings/cpython.py`** raw C-API bindings, reserved for the
  TPy-written library-type side (`PyRef`/registry/`PyType_FromSpec`), *not*
  the hot per-arg path.
- **Error path: NULL/-1 sentinel + a `try/catch` boundary scaffold built in
  slice 1** even though `answer`/`add` cannot raise (retrofitting the
  try/catch into every wrapper later is a rewrite). The scaffold also maps
  **built-in TPy exceptions -> their `PyExc_*` counterparts via a runtime
  C++ table** (free: `raise<E>()` already throws a catchable `BaseException`,
  no runtime change). User-defined exception *classes*, `@error_return`
  Err->raise, and panic->exception are **deferred** (the last needs the
  macro-gated throwing `TpyPanic` runtime change).
- **Build mode** reuses the existing compile/link path + `--no-main`; Python
  include dir via in-process `sysconfig`; `-DPy_LIMITED_API=0x030c0000`
  **from the first `.so`** (so a non-stable symbol is a compile error, never
  a retrofit); **Linux-only** for slice 1 (undefined Python symbols resolve
  at import; macOS `-undefined dynamic_lookup` is a small additive follow via
  a per-platform link-flag table); bare `<module>.so` (the `.abi3.so` tag is
  a packaging concern). The glue `.cpp` joins `all_cpp_paths`.
- **GIL: dropped from slice 1 entirely.** With no `nogil` and no user-facing
  `PyRef` (Q3), a `gil_held` flow-fact is a tautology that produces zero
  diagnostics. The only invariant kept: cpython bindings stay
  **metadata-declarable**, so `requires_gil` slots in when `nogil` is built.
  That property -- not a no-op check now -- is what keeps `nogil` "purely
  additive."
- **Facade: hand-mirrored `tpy/interop/cpython_h.hpp`, no `Python.h` in any
  real TU.** The marshalling helpers intrinsically mix `tpy` types and
  `PyObject*`, so the facade buys real macro-collision safety exactly there,
  not just hygiene. Guard the hand-mirrored struct layouts with a
  **`static_assert` self-check TU that includes real `Python.h`**, compiled
  as a build-time test and never linked into the shipped `.so`. Slice-1
  symbol set is small and all stable-ABI: opaque `PyObject`, `PyMethodDef`,
  `PyModuleDef`, `PyModule_Create`, `PyArg_ParseTuple`, `PyLong_*`,
  `PyErr_SetString`, `PyExc_*`. (`Py_buffer` waits for phase 3.)
- **Verification: two runs, identical driver.** A plain `driver.py`
  (`import main; print(main.add(2, 3))`) runs unchanged against both the
  built `.so` (new **ext-exec** variant) and the TPy *source* interpreted by
  CPython (`# tpy: ext_module` is a comment, `@export` resolves to a
  `lib/cpy` identity stub) -- giving TPy-vs-CPython parity for free. Build
  the thread with a **throwaway script first** (de-risk the `.so` build in
  hours), *then* harnessify. Defer the exec-cache extension; guard CPython
  >= 3.12. Action item: verify the `lib/cpy` `@export` stub works bare in an
  ext module.
- **Glue mechanics:** single-phase init (`PyInit_` returns
  `PyModule_Create`, calling `__tpy_init` for module globals);
  `METH_VARARGS | METH_KEYWORDS` (defer `METH_FASTCALL`);
  `PyArg_ParseTupleAndKeywords` splits args + kwargs into `PyObject*` slots
  (`"OO...:name"`, the `:name` suffix so a parse error names the callable)
  against a `kwlist` of the param names, so an exposed callable has Python's
  positional-or-keyword semantics; a zero-arg callable stays `METH_NOARGS`.
  (An arg-count / unknown-keyword error still raises the right *type*
  -- `TypeError` -- but the C parser's message text differs from CPython's own
  argument parser; a caller inspecting `str(e)` sees different wording, the
  same kind of cosmetic divergence as the scalar marshallers' error messages.)
  **`from_py<T>` owns every conversion -- never format codes**
  (format codes don't generalize to BigInt/records/str and would create a
  second, competing marshalling path). The keyword wrapper's 3-arg shape is
  cast into the `PyCFunction` slot via the facade's `as_pycfunction` (CPython's
  own `_PyCFunction_CAST` idiom, warning-clean under `-Wcast-function-type`).
  Param forms the unpack can't cross -- defaults, `*args`/`**kwargs`,
  positional-only (`/`), keyword-only (`*`) -- are rejected at compile time
  (loud, not silently mishandled); keyword-only + defaults are the next rung.

### Rung 2 -- BigInt marshalling under the 3.12 limited API

The limited API hides the fast bigint paths (`_PyLong_From/AsByteArray` are
private; `PyLong_As/FromNativeBytes` are 3.13+, below the floor), so the
design is **two-tier** both directions:

- **Fast path (the common case):** `PyLong_AsLongLongAndOverflow` /
  `BigInt::to_i64_checked` -- near-native for values that fit int64.
- **Slow path (genuine bigints): hex string round-trip, not decimal.**
  Decimal is poisoned by CPython's `int_max_str_digits` guard (default 4300
  digits -- `PyObject_Str` on a larger int *raises*, a silent parity cliff);
  power-of-2 bases are exempt. In: `PyNumber_ToBase(obj, 16)` ->
  `PyUnicode_AsUTF8AndSize` -> `BigInt::from_hex_str`. Out: `to_hex_string()`
  (already exists) -> `PyLong_FromString(hx, NULL, 16)`.
- **Liberal coercion:** `PyNumber_Index` on the way in (accepts
  `__index__` / numpy scalars; genuine non-integers still -> `TypeError`).
- **Two small runtime additions**, each mirroring an existing pattern and
  unit-testable without CPython: `BigInt::from_hex_str(std::string_view)`
  (parse `[-]0x<hexdigits>`, base 16 -- `from_str` is base-10 only) and
  `bool BigInt::to_i64_checked(int64_t&) noexcept` (mirrors the existing
  `to_uint64_checked`, so the `to_py` fast-path test does not throw
  `FixedIntOverflow` for control flow).
- Slow-path refcount discipline: copy out of the `PyNumber_ToBase` string's
  UTF-8 buffer *before* `Py_DECREF`-ing it.

### POC build order (IR exposure -> near zero)

IR-immune parts first (pure win, no migration risk): the facade header + the
C++ template marshallers + the two `BigInt` runtime additions + the `.so`
build mode + the `@native` bindings. Thin compiler hooks last, kept minimal
and fact-on-node: the `ext_module` directive, the `@export` reinterpretation
+ `exposed_to_host` marker, and the sibling `generate_extension_glue()`.

## The governing constraint: the representation gap

TPy's value proposition is *unboxed, monomorphized, homogeneous* data:
`list[int]` is a `vector<BigInt>`, a record is a flat C++ struct, `str` is
a `string_view`. CPython is *boxed `PyObject*` everywhere*, refcounted,
heterogeneous.

These representations cannot be *shared* live. Handing Python a
`vector<BigInt>` and having it see a `PyList` would require boxing every
element. So:

> **CPython interop is a boundary-marshalling story, not a shared-memory
> story.** Every value that crosses pays a marshal (copy + box/unbox),
> per element for containers.

This single fact frames everything. Interop is cheap exactly where the
boundary is *thin* (primitives, bulk numeric buffers) and expensive or
semantically fraught where it is *thick* (nested containers, live mutable
objects, callbacks). The design leans into the thin cases first -- which
is also the "make this kernel fast" case that matters for the
performance-critical audience.

**Perf note (performance angle).** The speed win is the buffer/numeric path, not
arbitrary `int` kernels: TPy `int` is `BigInt` (arbitrary precision), so
`PyLong <-> BigInt` marshalling and BigInt math are *not* the fast path --
steer hot kernels to `Int32`/`Int64`/`float`. And v1 kernels run with the GIL
*held* for the whole call (no parallelism until `nogil`, phase 7) -- still a
native-loop speedup, but single-threaded.

## The shared marshalling layer (the keystone)

The single most important architectural decision: `to_py` / `from_py` are
**one direction-agnostic layer**, not part of the extension glue. The same
functions serve both directions:

- extension mode: `from_py` on the way in, `to_py` on the way out;
- embedding mode: `to_py` on the way in, `from_py` on the way out.

Building it standalone from day one is what makes leading with extension
cost embedding nothing later. It is *the* hook to reserve.

### Per-type mapping

| TPy | Python | Cost | Note |
|---|---|---|---|
| `int`/BigInt, `float`, `bool` | PyLong / PyFloat / PyBool | O(1) small int | trivial |
| `str` / StrView | PyUnicode | O(n) out; **copy-in (done)**, borrow later | see "str/borrow"; `str` done, `StrView` -> 3.5 |
| `bytes` / BytesView | PyBytes | O(n) out; copy-in (done), borrow later | see "str/borrow"; `bytes` done, `BytesView` -> 3.5 |
| `Span[T]` numeric | buffer / `memoryview` / ndarray | O(n) copy-in (v1.0); zero-copy later | see "buffer protocol" |
| `list`/`dict`/`set`/`tuple` | PyList / PyDict / ... | **O(n)*elem, by-copy** | see "container cliff" |
| record / class | extension-type wrapper | O(1) pointer | see "classes" |
| `Optional[T]` / None | None | O(1) | |
| `A \| B` union | tag dispatch | varies | |

> `str` / `PyUnicode` round-trip fidelity depends on the build's string width
> (`STRING_WIDTH_DESIGN.md`): width 4 round-trips any Python `str` losslessly,
> so Unicode extensions build at width 4; a narrower build (e.g. width 1 /
> Latin-1) accepts only strings in its range and errors otherwise.

### Ownership rule

A `PyObject*` entering TPy is an owned, refcounted resource -> model it as
`Own[PyRef]` (a `@nocopy` wrapper whose `.clone()` is `Py_INCREF` and whose
drop is `Py_DECREF`). Borrows of its *contents* (a str buffer, buffer
memory) are valid only within a scope provably outlived by the held ref.
Going out: TPy reference types become owned PyObject wrappers; value types
copy into fresh PyObjects. This folds entirely into the existing Own/borrow
model -- no new ownership concept.

### The str/borrow insight (and why it generalizes)

A `str` argument *into* an extension function can be a **zero-copy
`StrView` borrow**: Python owns the immutable buffer and holds its ref
across the call, so the borrow is valid for the call's duration. Only if
TPy *stores* it past the call must it copy to owned `str`. The
promote-borrow-to-owned-on-escape machinery already exists -- but it was
built for *TPy-owned* sources, so a borrow whose backing is *foreign* memory
(kept alive by Python for the call) is a genuine **new primitive**, not free
reuse (see "Design validation"). The buffer protocol (next) is the same idea
for numeric data and shares that gap.

**v1 default: copy-in (implemented).** For `str`/`bytes` args, v1 *copies on
entry* (always sound): the marshaller produces the owned form (`std::string` /
`std::vector<uint8_t>`) and the generated wrapper passes it to the function's
borrow-form param (`std::string_view` / `std::span<const uint8_t>`) by implicit
conversion, the owned local outliving the call -- so the boundary marshals a
type's *owned* form (`to_cpp()`) with no special-casing in the glue emitter.
The borrow forms `StrView`/`BytesView` are rejected for now (they need the
foreign-borrow primitive); the zero-copy borrow is the deferred optimization
that lands with it (phase 3.5). The rest of this section describes that end
state, not the v1 behavior.

### The buffer protocol -- copy-in in v1.0, the high-value zero-copy path later (phase 3.5)

**Implemented (the Span rung).** `Span[readonly[T]]` and `Span[T]` (`T` a
fixed-width int or `float`) bind to any object exposing the buffer protocol
-- `array.array`, `memoryview`, `bytes`/`bytearray`, numpy arrays -- as an
`@export` FUNCTION **parameter only** (never a return type -- return numeric
data via `list[T]` instead; see "Resolved (Q2)" below).

- `span_from_py<T>` (`runtime/cpp/include/tpy/interop/marshal.hpp`) calls
  `PyObject_GetBuffer` requesting `PyBUF_ND | PyBUF_FORMAT` (shape + format,
  no strides -- the exporter must present a C-contiguous 1-D buffer or the
  call fails), validates `itemsize`/`ndim`/`format` strictly against `T` (no
  coercion across width/signedness/int-vs-float -- the same strict-by-kind
  family as containers/enums; a format/itemsize mismatch or a non-contiguous
  buffer is a `TypeError`), copies the bytes into a fresh `std::vector<T>`,
  and releases the buffer view before returning.
- The owned `std::vector<T>` implicitly converts to the function's
  `std::span<T>`/`std::span<const T>` param at the call site -- the same
  "owned local outlives the call" trick `str`/`bytes` use for
  `string_view`/`span<const uint8_t>` (no borrow/storage-form special-casing
  in the glue emitter).
- **v1.0 = copy-in (spike outcome), for BOTH `Span[T]` and
  `Span[readonly[T]]`.** The intended zero-copy path needed a *non-escaping*
  foreign `Span` borrow, but the ephemeral-borrow spike showed the escape
  machinery can't fence a foreign-source param at all sinks (see
  "Alternatives and spike outcomes" / Q6). So **v1.0 copies the buffer in
  unconditionally** -- there is no `PyBUF_WRITABLE` request and no write-back,
  even for a mutable `Span[T]` param; zero-copy input (read-only and
  writable) is deferred to phase 3.5 with the foreign-borrow primitive.
- **Acknowledged divergence: mutation through a `Span[T]` (non-readonly)
  param is invisible to the caller.** Unlike the container cliff (where the
  annotation never promised write-back), a *mutable* `Span[T]` -- as opposed
  to `Span[readonly[T]]` -- is TPy's own signal that the view is writable,
  so copy-in defeats that promise more sharply than a copied container does.
  The compiler WARNS wherever sema proves the param is mutated (the same
  `mutated_params` mechanism and bar as the list/dict/set container-mutation
  warning -- no escape hatch in v1, matching that precedent); a
  `Span[readonly[T]]` param can never trigger this (writing through it is
  already a compile error), and a read-only *use* of a mutable `Span[T]` has
  no observable divergence and stays quiet.

**Resolved (Q2):** *returning* a `Span` to Python is harder (TPy would
have to export its own buffer or copy). v1 accepts buffers as input only and
returns owned copies via `list[T]`; TPy-as-buffer-exporter is deferred.

### The container cliff (a declared divergence)

`list[int]` <-> PyList is a full O(n) marshal that produces a **copy** --
the two sides do **not** alias, so mutations do not propagate. This is a
CPython-parity divergence, but an *inherent, declarable* one (the project
rule: declared divergence is acceptable, silent is not). The doc states it
plainly and steers bulk/numeric users to `Span` + buffer protocol, which is
the same by-copy model in v1.0 (see "The buffer protocol" above) but is the
path zero-copy input lands on first (phase 3.5).

We do **not** build alias-preserving lazy proxies in v1 (a PyObject
wrapping the vector, boxing per `__getitem__`) -- high complexity, and it
fights the unboxing that is the whole point.

**Implemented (the container rung).** `list`/`dict`/`set`/`tuple` cross as
`@export` function params/returns, marshalled O(n) by-copy recursively:
`std::vector` <-> PyList, `tpy::ordered_map` <-> PyDict (insertion order),
`tpy::ordered_set` <-> PySet, `std::tuple` <-> PyTuple. Elements are the
scalar/str/bytes leaves and arbitrarily nested containers of those. The
recursion is **glue-driven**, not C++-template-driven: the C++ storage type
is ambiguous at the leaves (`list[bytes]` and `list[list[UInt8]]` both render
`std::vector<std::vector<uint8_t>>`), so only the codegen glue -- which holds
the unambiguous TPy element types -- can pick the right leaf marshaller. The
`marshal.hpp` helpers (`list_from_py`/`list_to_py`/...) take a per-element
conversion callable; `extension.py` emits nested lambdas bottoming out at
`from_py<leaf>`/`to_py`.

Three acknowledged, documented divergences from the aliasing CPython source:

- **Copy-in mutation (the cliff itself).** A param is an owned copy, so a
  mutation through it (`append`, `d[k] = v`) is not visible to the caller. A
  list/dict/set param that sema proves is mutated **warns** at the `@export`
  function (`mutated_params`); a read-only container param has no observable
  divergence and stays quiet. tuple is exempt (value type, immutable). There
  is no write-back escape hatch in v1; the zero-copy *read* path is `Span` +
  buffer protocol (phase 3.5). A tuple whose element is itself a mutable
  container (`tuple[list[int], str]`) does not warn either, and mutating the
  inner container is likewise invisible to the caller -- but this is the
  pre-existing TPy value-vs-reference model (a tuple is a value type, so `p[0]`
  copies the inner list on access; a pure-TPy caller wouldn't see the mutation
  through the tuple either), *not* a new boundary divergence, so there is
  nothing for the warning to fire on.
- **Strict-by-container-kind IN.** A non-list / non-dict / non-set / non-tuple
  arg (or a tuple of the wrong arity) is a `TypeError`, where the annotation-
  ignoring source would accept any iterable. Same class as the existing
  enum/str/bytes strict-IN contract.
- **Per-element scalar coercion.** Each element converts through the same
  `from_py<leaf>` the scalar params use, so `list[int]` coerces `True -> 1`
  (`__index__`) and `list[bool]` coerces via truthiness. A `dict[int, ...]`
  marshalled from `{True: ...}` returns a Python `int` key (`True == 1`, but
  `type(k)` flips) -- and two keys that collapse under the leaf conversion
  (`{1, True}` -> `set[int]`) merge.

Exposed class/enum types are **not** yet admitted as container elements (the
per-element converter has no module type handle to round-trip through);
`list[SomeExportedClass]` is rejected with the unmarshallable diagnostic.

## abi3 / limited API commitment

v1 targets the **limited API / stable ABI** (floor **3.12**, empirically
validated -- a real `Py_LIMITED_API` extension compiled, imported, and ran;
see "Alternatives and spike outcomes"), for two reasons that compound:

1. **One wheel, all CPython versions** (`cp3X-abi3-<plat>` tag) -- a plain
   Python user `pip install`s and `import`s, never touching TPy.
2. **It makes the facade discipline feasible.** TPy must keep `Python.h`'s
   macro soup out of generated TUs (same hazard the pcre2 facade avoids).
   The limited API treats `PyObject` as **opaque, accessed via functions
   not macros**, so a facade header (`tpy/interop/cpython_h.hpp` mirroring
   only what we use; real `Python.h` only in a separate `.c` TU) stays
   clean. The minor cost -- function calls vs inline macros -- is noise at
   a marshalling boundary.

Consistent with the project aesthetic ("types in Python, tiny C++
helpers"): the C-API binding is a thin `@native` layer
(`lib/tpy/_bindings/cpython.py`), and per-type marshal wrappers are
*generated*.

## Extension mode mechanics

- **Declaration:** a module marker `# tpy: ext_module` (a near-free addition
  to the directive registry, analog to `native_module` / `macro_module`).
  Exposure is **explicit opt-in** so the ABI surface is never accidental --
  but note `@export` *already exists* (meaning C-ABI `extern "C"`, and
  forbidden in `native_module`), so CPython exposure must *extend* that
  linkage machinery (a `binding=` mode or a distinct linkage; CPython export
  needs far more than `extern "C"`), not introduce a new decorator. Exposing
  **classes** is the one genuinely new piece (no `RecordLinkage.EXPORT`
  today). See "Design validation".
- **Codegen (a new, backend-relative emit mode):** the normal `.hpp`/`.cpp`
  for the TPy logic is unchanged, plus a glue TU: per-function
  `PyArg`-parse -> `from_py` -> call -> `to_py` wrappers, the
  `PyMethodDef[]` table, `PyModuleDef`, and `PyInit_<name>`; extension-type
  definitions for exposed records.
- **Build / packaging:** TPy as a PEP 517 build backend
  (`turbopython.build`); `uv build` / `pip wheel` produce the abi3 wheel;
  the header-only runtime compiles in (no TPy install for consumers).
  Linux does not link libpython (resolved at load); Windows would need
  clang/MinGW (MSVC is unsupported -- no GCC statement expressions).

### The four v1 exposure kinds

**1. Free functions** -- easiest. Each becomes a generated wrapper once the
marshalling layer exists. The exception bridge is needed even here.

**2. Classes + methods** -- the meat, but mechanical. Each exposed record
becomes a CPython heap type (`PyType_FromSpec`, abi3-friendly). The TPy
struct lives *inside* the instance memory after the PyObject header;
`tp_init` runs the TPy constructor (placement-new over the GenericAlloc'd
storage; a re-`__init__` destroys the prior payload first), `tp_dealloc` runs
the C++ destructor (so `Own`/`Box`/`Rc`/container fields clean up correctly).
Payoff: because the
PyObject **owns** the instance, multiple Python references alias and see
mutations -- exposed classes get *correct Python reference semantics*,
unlike containers. A method call unwraps `self` to a borrow valid for the
call (same borrow story as `str`).
*Scope dial:* baseline = `__init__` + plain methods + annotated fields as
getset properties; then all four dunder groups (`__repr__`/`__str__`,
`__eq__`/`__hash__`, ordering + arithmetic operators, container protocol --
see Q4), landed roughly in that order. **Defer** inheritance of exposed
class *hierarchies* (each exposed class is flat in v1).

**3 + 4. Enums + constants** -- mostly bookkeeping at `PyInit`. **Implemented**
(this rung): an `@export` enum is rebuilt at `PyInit_` as a **real** CPython enum
via the stdlib `enum` functional API -- `enum.IntEnum(name, {member: value}, module=...)`
for an `IntEnum`, `enum.Enum(...)` for a plain `Enum` (the `runtime/cpp/include/
tpy/interop/enum_bridge.hpp` `make_enum` helper hides the import + call). Passing
`module=` pins `__module__`/`__qualname__` so the constructed type is observably
identical to a source-level `class Color(IntEnum)` (CPython-parity-verified: members,
`.name`/`.value`, singleton identity, iteration, value/name lookup, and the
IntEnum-vs-Enum `== int` distinction all match). Module-level `Final` constants of
a boundary scalar (`int`/`IntN`/`bool`/`float`/`str`) become module attributes --
a one-time snapshot read after `__tpy_init` (Final => immutable, so the snapshot
can't go stale). `@native` enums (values come from C++) and nested enums are
rejected with a located error; non-boundary `Final` types (`Char`, `tuple`) are
simply not exposed (like a non-`@export` function). An exposed enum is also a
valid `@export` **function param/return type** -- the value crosses as its
CPython member (`runtime/cpp/include/tpy/interop/enum_bridge.hpp`
`enum_from_py`/`enum_to_py`, against the module-static enum-type handle): OUT
reconstructs the member via `EnumType(value)` (singleton identity preserved), IN
is **strict by type** (the arg must be an instance of the enum; a bare int is a
`TypeError`, unlike a lenient `int` param and unlike the untyped Python source
where `f(1)` runs -- documented, ext-only-tested divergence). **Deferred:**
nested/cross-module enums (a cross-module enum param is a located error),
`bytes`/`BytesView` constants. Data-carrying (algebraic) enum variants do not
exist in TPy (members are always int-valued), so there is nothing to
wrapper-per-variant.

**Feasibility verdict:** all four are feasible. The dominant cost is the
shared marshalling layer and the extension-type generator; the four kinds
layer cheaply on top. Substantial (multi-week), not a research problem; the
scope dials above are how size is controlled.

## Exception and panic bridge (load-bearing even for free functions)

TPy has two runtime failure channels; both must bridge to CPython's
thread-state error model.

**Status:** the built-in-exception half is **implemented**. A built-in TPy
exception escaping an `@export` body (or `PyInit_`) crosses as the matching
`PyExc_*` type with `e.what()` as the message: the glue catches
`const tpy::BaseException&` and routes it through the runtime cascade in
`runtime/cpp/include/tpy/interop/exc_bridge.hpp` (`set_py_err_from` ->
`py_exc_for`), which maps the dynamic type most-derived-first and degrades an
unlisted subclass to its nearest listed base. Still **planned** from the list
below: `@error_return` Err -> raise and panic -> exception.

**User exception classes are also implemented** (for the type, not yet the
instance data). Each user exception class defined in an ext_module gets its own
Python type at `PyInit_` via `PyErr_NewException` (inheriting its built-in
base's `PyExc_*`, or an already-created user base, topo-ordered), added to the
module (`PyModule_AddObjectRef`) so it imports as `mymod.MyError`, and recorded
in an `ExcRegistry` (`typeid -> PyObject*`) the glue passes to `set_py_err_from`;
the registry is consulted by exact dynamic type before the built-in cascade, so
a user `class MyError(ValueError)` surfaces as `MyError` (catchable as
`ValueError` too) instead of degrading. `PyErr_SetString(type, e.what())` still
carries only the message field, so a **data-carrying** user exception (instance
fields beyond the message) crosses by type + message field only -- its fields,
and the full `args`/`str()` for a multi-arg constructor, do not cross. A direct
`raise DataExc(...)` in an `@export` body warns. Faithful per-field crossing
(the `PyErr_SetObject` + per-field marshalling path) is the deferred next rung;
so are transitive-raise detection and cross-module user exceptions.

- **Exceptions (`raise` / `except`).** A TPy `raise` that reaches the
  boundary sets a Python error and the wrapper returns the C-API error
  sentinel (`NULL` / `-1`). Built-in TPy exception types map directly to their
  Python counterparts (`IndexError`, `ValueError`, ...). User exception types
  get a generated Python class per type at `PyInit` (`PyErr_NewException`),
  preserving the inheritance chain; a runtime registry (TPy exc type ->
  `PyObject*` class) drives the boundary `catch`. **As implemented today this
  uses `PyErr_SetString(type, e.what())`, so only the type and message field
  cross.** The full-fidelity form -- `PyErr_SetObject` over a constructed
  instance with data fields marshalled to instance attributes -- is the
  deferred next rung (see the status note above and `TODO.md` 1a). See
  "Resolved design questions" Q1 for the full mechanism.
- **`@error_return`** functions exposed across the boundary: the `Err`
  branch surfaces as a *raised* Python exception (the natural Python idiom),
  the `Ok` branch as the unwrapped value.
- **Panic (`tpy_panic`).** A panic would otherwise `abort()` the *host
  interpreter* -- hostile. **Decision: at the CPython boundary a panic is
  converted to a Python exception** (`SystemError`, or a dedicated
  `TpyPanic`), not an abort. This bends the "panic = unrecoverable" model,
  but aborting the host is unacceptable. The conversion happens only at the
  boundary wrapper; intra-TPy panic semantics are unchanged. **Spike outcome:**
  `tpy_panic` is `std::exit(1)` today (uncatchable), so this needs a runtime
  change -- a macro-gated throwing `TpyPanic` (non-`BaseException`) for `.so`
  builds, plus a `catch (...)` audit; default builds keep `std::exit(1)`.
  Effort S. See "Alternatives and spike outcomes".

## The GIL capability model

### The hazard

When CPython calls an extension, the interpreter holds the GIL; while held,
PyObjects may be touched. Real parallelism requires *releasing* the GIL,
running pure-native work, then re-acquiring. **Iron rule: while the GIL is
released, no PyObject may be touched** (read, write, incref, decref, call).
Violations are data races and heisenbug crashes -- and completely
invisible. This is the top footgun in C/Cython extensions.

### How the leaders handle it

- C extensions: zero checking.
- Cython (`nogil`): partial -- some calls are rejected, but you can still
  segfault.
- PyO3 (Rust): a *compile error*. A `Python<'py>` capability token gates
  nearly every C-API call; `py.allow_threads(|| ...)` runs without the token
  so the type system forbids GIL-requiring calls inside, and PyObject refs
  are lifetime-bound so they cannot leak in.

### The TPy model: an ambient capability tracked as a flow fact

This is a **flow-sensitive fact**, the same family as `flow_facts.py`'s
existing `init_terminated` / narrowing state (set on a branch, merged at
joins) -- *not* the Send/Sync / `frame_send` family, which is flow-insensitive
type-trait inference over type shapes (the two share vocabulary, not
machinery; keeping them straight matters so implementation targets the right
subsystem). Model:

- A **`Gil` capability** = "this thread currently holds the GIL." Every
  *Python-touching* operation requires it.
- A scoped **`with nogil:`** block *removes* the capability for its body, so
  any Python-touching op inside is a **compile error** (the way a moved-out
  `Own` becomes unusable, or `readonly` removes mutation). A `PyRef` may
  neither be used inside (using one needs `Gil`) nor escape into it (existing
  escape analysis).
- A nested **`with gil:`** re-acquires (the Cython inverse) for callbacks
  into Python from within `nogil`.

**Ambient, not a token parameter.** Unlike PyO3's explicit `py` argument,
the capability is a **flow-sensitive fact** (`gil_held`) carried in the
`flow_facts.py` per-branch snapshot: `true` at extension entry, `false`
inside a `nogil` body, restored after; `with gil:` sets `true`. This is
PyO3-grade safety with Python ergonomics and no token-passing noise -- the
north star.

**"Python-touching" is library-declared, not hardcoded.** Rather than an
`if`-chain in codegen, the cpython binding functions declare
`requires_gil=True`, and the checker propagates that fact (matching the
"push per-op properties into library-declared metadata" guidance). The
Python-touching set is then exactly: construct/convert a PyObject, incref/
decref, call a Python callable, `PyErr_*`, touch module-level Python state.
Everything native (values, vectors, BigInt math, `Span` over raw memory) is
GIL-free by construction.

### Layering: v1 ships the demand, not the removal

The capability earns its keep only once extensions *release* the GIL (the
parallelism feature). A single-threaded "make it fast" extension never
releases, so the capability is trivially always-present and the check is a
no-op. Therefore:

> **v1: the capability exists and is ambient-always (no `nogil` blocks yet),
> but the marshalling layer and PyRef ops *require* it by construction.**

Then `nogil`/`with gil` later is purely additive: add the *removal*
mechanism (strip the ambient fact for a scope) + the escape check. The
marshalling layer does not change -- it already demands what `nogil`
withholds, so code that marshals inside a `nogil` block fails to typecheck
automatically, with no new plumbing.

v1 GIL work is thus small insurance: (a) the capability exists, (b) it is
ambient-always, (c) marshalling + PyRef ops require it. None of
`nogil`/`with gil`/escape-out-of-nogil is built in v1.

### The diagnostic bar

Ambient capability is more "magic" than an explicit token, so error quality
is part of the feature's definition of done:

```
error: cannot use Python object `on_done` while the GIL is released
 5 |     with nogil:
   |          ----- GIL released here
 7 |         on_done(result)
   |         ^^^^^^^ calling a Python object requires the GIL
help: re-acquire the GIL:  with gil: \n    on_done(result)
```

### Illustrative source (future, with nogil)

```python
@export
def parallel_sum(data: Span[readonly[float]]) -> float:
    with nogil:                  # GIL released
        total = 0.0
        for x in data:           # OK: Span over raw memory, not a PyObject
            total += x
    return total                 # GIL re-held; float -> PyFloat marshal is fine
```

## Reserved hooks (do not lock these out)

- **Embedding direction.** Covered by the direction-agnostic marshalling
  layer + `Own[PyRef]` + the GIL capability + the bidirectional exception
  bridge -- all built for v1 anyway. Embedding adds the *entry* side
  (acquire-GIL-and-marshal-in) and a libpython link mode; no marshalling
  rework.
- **Callbacks / opaque passthrough** (`obj: PyObject`, `on_done:
  PyCallable`). `PyRef` is essentially internal in v1; user-held PyRefs are
  this feature. The GIL model already covers them uniformly.
- **Async <-> asyncio.** Out of scope for v1, **eventually implemented.**
  Keep the boundary wrappers free of any "synchronous-only" assumption so a
  future awaitable-returning export can slot in. Separate deep design.
- **Free-threaded CPython (PEP 703, 3.13+).** No-GIL builds change the GIL
  model (the `gil_held`/`nogil` design) fundamentally. Confirmed by the abi3
  spike: **abi3 and free-threading are currently mutually exclusive** -- the
  stable-ABI flag is ignored on free-threaded builds and `abi3t` is unfinished
  (PEP 803), so a free-threaded `.so` must be built **per-version (non-abi3)**.
  Keep the GIL capability abstract enough that a free-threaded target can map
  it to per-object locking / "always parallel" rather than a single global lock.
  Relevant to the parallelism angle; revisit when `nogil` (phase 7) is
  designed.

## Design validation (architecture fit)

Six read-only investigations checked this design against the actual `tpyc`
sources. Headline: **the design is feasible and rests largely on existing
machinery** -- three assumptions hold with only minor caveats, two needed
corrections (folded in above), and one surfaces a real architectural
dependency.

**1. Markers / `@export` -- Holds, with a refinement.** `# tpy: ext_module`
is a near-free directive addition (`_DIRECTIVE_SPECS` + a `ModuleDirectives`
field + one branch in `parse/parser.py`). Decorators are recognized by
resolved qname via library-declared stubs, so a marker is mechanical -- *but*
`@export` already exists (C-ABI `extern "C"`, forbidden in `native_module`).
CPython exposure must extend that linkage machinery, and exposing **classes**
needs a new `RecordLinkage.EXPORT` (functions-only today). *Implication:*
reuse/extend `tpy.extern.export`, don't add a decorator.

**2. Codegen emit mode -- Holds with caveats.** Output shape is decided by
what codegen's `generate()` returns; the driver already tolerates variance
(`native_module` returns `("","")`; cycle members emit an *extra* fwd header
via `generate_fwd_header`), and `all_cpp_paths` is a single link-set
chokepoint. The `--no-main` / `__tpy_init` / `__tpy_main` split is *directly*
the split an extension needs (`PyInit_` replaces `main`, still calls
`__tpy_init`). *Implication:* add a sibling `generate_extension_glue()`
(mirroring `generate_fwd_header`) rather than widening the load-bearing
`(hpp, cpp)` tuple. The actual `.so` build is new build-config surface (see
"biggest net-new" below).

**3. `Own[PyRef]` / `PyCallable` as library types -- Holds with caveats.** A
new instance of the `Rc` pattern, *not* a new compiler feature: `@nocopy`
move-only emit, `__del__`-into-destructor with the `__tpy_owned_` double-drop
guard, opaque `@native` types behind `Ptr`, and `interior[]` mutate-through-
readonly all exist; `.clone()` is an ordinary method, so `clone()=Py_INCREF`
and `__del__=Py_DECREF` are just bodies over a tiny C++ facade. *Caveats:*
sharing must go through `.clone()` (the `Rc(other)` sharing-ctor gap applies);
whether `.clone()` must work through a `readonly[PyRef]` decides if the
`interior[]` hatch is needed.

**4. GIL as a flow fact -- Holds (correction applied).** `gil_held` is a
natural new boolean `FlowFacts` field modeled on `init_terminated` (AND-merge,
free loop/join handling). The correction: it is *not* the Send/Sync family
(those are flow-insensitive type-trait inference). *Caveat:* `with` blocks do
not currently save/restore flow facts, so `with nogil:` / `with gil:` needs
new save/set/restore scaffolding in `_analyze_with` (straightforward given the
if/loop machinery, but genuinely new wiring). Lowest-risk phase, and unchanged
for v1 (ambient-always).

**5. Facade pattern -- Holds with caveats.** The pcre2 facade header +
`@native` bindings transfer mechanically (`@native` already supports every
binding form CPython needs; abi3 keeps the no-macros invariant). The
`managed=True` / vendoring half does *not* transfer and should be dropped --
CPython is the host, not a bundled dep (no source, no SHA, no
`bundled/system/auto`). The only uncovered need is the shared-extension link
mode (below).

**6. Foreign-source borrow + Span -- Holds with caveats; the real
dependency.** Raw-pointer `Span(ptr, len)` over non-TPy memory *already exists
and is tested*, so the buffer-protocol Span is mostly a `from_py` front door
(extract `Py_buffer` -> `ptr+len`). But the zero-copy *borrow* story (str args
and buffer views valid for the call, promoted on escape) needs a primitive TPy
lacks: a **foreign borrow** -- a borrow whose backing is external memory
asserted valid-for-the-call. Today `return_borrows_from` is a *param-index*
domain (cannot name a foreign source) and `BorrowTracker` is string-keyed over
TPy storages. This is the *same* hole as lifetime-checked `@native -> V`
returns (`BUGS.md` borrow section; `docs/ESCAPE_ANALYSIS_DESIGN.md` "declared
borrow contracts for precompiled bodies") and belongs against the MIR
`Place`/`LoanInfo` plan, not bolted onto the string-key tracker.

### The two biggest net-new pieces

Everything else reuses existing machinery; two items are genuinely new, and
each recurred across investigations:

- **A CPython-extension build-output mode** (surfaced by #2 and #5): emit a
  `PyInit_`-exporting `.so` -- `-shared`, Python include dir, allow-undefined
  Python symbols on Linux/macOS, link `pythonXX.lib` on Windows, suppress
  `main`. This is the `ext` target / PEP 517 backend already reserved in
  `PROJECT_TOOLING_DESIGN.md`; it is *not* a `link()` extension.
- **A foreign-borrow lifetime primitive** (#6): only needed for the *zero-copy*
  str/buffer borrow path; a pre-existing escape-analysis gap, not
  interop-specific.

### Scope refinement this forces

Zero-copy borrow (promote-on-escape for str args; a non-escaping foreign
`Span` for buffer input) is gated on the foreign-borrow primitive (ultimately
the IR/region work). The ephemeral-borrow spike **confirmed** the cheap
interim can't make a foreign `Span` param sound -- the escape machinery treats
params as caller-owned/long-lived, and 0/8 sinks reject a foreign borrow. So
**v1.0 copies everything in** -- `str`/`bytes` args *and* buffer input (always
sound; matches the container-copy story). Zero-copy is the first post-v1.0
work (phase 3.5). All of v1.0 thus stays free of the IR dependency.

## Code organization, build, and testing

### Code organization

Most of the surface is **library + runtime, not compiler** -- consistent with
"types in Python, tiny C++ helpers" and the IR-migration guidance (keep facts
on AST nodes, don't sprawl interop logic across phases):

- **Library (the bulk).** `lib/tpy/_bindings/cpython.py` -- raw `@native`
  C-API bindings (`Py_INCREF`/`Py_DECREF`, `PyLong_From*`,
  `PyObject_GetBuffer`, `PyErr_*`, `PyType_FromSpec`, ...).
  `lib/tpy/tpy/interop/` -- TPy-native wrappers: `PyRef`/`PyCallable` (the
  `Rc`-pattern library types), marshalling helpers, the `@export` binding stub.
- **Runtime facade.** `runtime/cpp/include/tpy/interop/cpython_h.hpp` -- the
  hand-written limited-API facade (opaque `PyObject`, function decls, no
  `Python.h`).
- **Codegen (isolated).** `tpyc/codegen_cpp/extension.py` (`ExtensionGenerator`,
  wired as `CodeGenerator.extension`) owns glue-TU emission: per-function
  `from_py`/`to_py` wrappers, `PyMethodDef`/`PyModuleDef`/`PyInit_`,
  `PyType_FromSpec` for classes, exception-registry init. Invoked via the thin
  `CodeGenerator.generate_extension_glue()` delegator, so the load-bearing
  `(hpp, cpp)` codegen contract is untouched. The shared boundary helpers
  (`boundary_cpp_type` / `boundary_unmarshallable_msg`, beside
  `is_boundary_marshallable`) live in `type_def_registry.py` so the sema
  validator and the glue import them from one neutral home.
- **Thin compiler hooks.** Parser: the `ext_module` directive + the `@export`
  linkage extension (small touches to `_DIRECTIVE_SPECS` / the linkage map).
  `cli.py` / `BuildLayout`: the `.so` build-output mode. Everything reads facts
  off AST nodes -- no new analyzer->codegen side tables.

Net: a codegen **backend mode inside `tpyc`** (not a separate top-level
package), with the bulk pushed to library/runtime and the core getting thin,
fact-driven hooks.

### Build & packaging (user-facing)

Two paths, both reusing the Python ecosystem; mechanics live in
`PROJECT_TOOLING_DESIGN.md` (the `ext` target + PEP 517 backend, H2):

- **Primary: TPy as a PEP 517 build backend.** `pyproject.toml` sets
  `[build-system] build-backend = "turbopython.build"` and marks the ext
  modules; `uv build` / `pip wheel` invoke `tpyc` and package the `.so` into an
  **abi3 wheel** (`cp3X-abi3-<plat>`, one wheel for all CPython versions).
  Downstream users `pip install` + `import` -- **no TPy install required** (the
  runtime is header-only, compiled in).
- **Dev loop: the porcelain.** `tpx` builds an `ext` target locally (a `.so` in
  place) for quick `python -c "import mymod"`, no packaging. Optional --
  `uv build` must work on its own.

Build needs the C++ toolchain + Python dev headers (`sysconfig` include path) +
the `.so` link mode (phase 2). Binary-wheel distribution (manylinux, etc.) is
standard Python packaging, unchanged by TPy.

### Testing (ext-exec snapshot harness -- IMPLEMENTED)

Interop tests ride a snapshot harness modeled on the main one, with the
**same TPy-vs-CPython parity check** the `cpy` phase gives -- the right safety
net for interop. Shipped as a dedicated `tests/test_interop_exec.py` over
`tests/interop/<case>/` (the ext-exec cases have a different shape -- a bare
`<mod>.py` + `driver.py`, no `src/main.py` -- so they get their own module
rather than folding into `tests/cases/`); the comp-phase rejection cases stay
under `tests/cases/interop/`. See `tests/interop/README.md`.

- **Layout.** An `ext_module` `<mod>.py` (compiled) + a plain Python
  **`driver.py`** that imports and exercises it
  (`assert mod.add(a, b) == ...; print(...)`), plus an optional `ext_checks.py`
  for ext-only marshalling-error cases. The driver is *identical* across both
  runs below; only what `import <mod>` resolves to changes.
- **comp/snapshot** (always): snapshot the generated `.hpp`/`.cpp` **and the
  glue TU** (`<mod>_ext.cpp`) into `expected/`.
- **ext-exec** (cached, like the exec phase): build the `.so` via the
  first-class `.so` build mode (`tpyc -b` on an `# tpy: ext_module`), run the
  driver under CPython importing it, compare stdout to `output.txt`. Gated by
  a content-addressed marker in the shared `exec-results/` cache.
- **cpy parity** (always): run the *same* driver with
  `PYTHONPATH=lib/cpy:<case>` so `import <mod>` loads the TPy **source**
  interpreted by CPython; compare the same `output.txt`. The compiled
  extension must behave like its source-as-Python. The `lib/cpy/tpy/extern.py`
  stub already makes `@export` an identity and `ext_module` a no-op.
- **facade self-check** (`test_facade_selfcheck`): compile the hand-mirrored
  facade against the real Python ABI once; skipped when `Python.h` is absent.

This driver-based parity is also the reference-vs-extension shape that
satisfies the project's reference-type test-adequacy rule (mutate-and-observe
across the boundary). Building the `.so` needs only the toolchain (facade
only -- no `Python.h`); the self-check additionally needs `Python.h`.

## Alternatives and spike outcomes

All three pre-implementation spikes have been run. Results below; the rest of
the doc reflects them.

**Alternative -- nanobind vs hand-rolled glue: RESOLVED (hand-rolled).** A
spike weighed emitting nanobind (header-only, covers most of phases 2-7)
against hand-rolling the raw C-API. Decision: **hand-rolled**, on three
grounds -- (1) TPy is a code *generator*, not a human, so nanobind's
hand-binding ergonomics buy little while inverting control over a surface TPy
already knows statically; (2) the project values minimal/auditable C++ and is
already C++-back-end-bound -- nanobind's template instantiation adds compile
time + a vendored dep; (3) nanobind has **no abi3 under free-threading** (the
stable-ABI flag is ignored on free-threaded builds; `abi3t` is unfinished --
PEP 803), so owning the glue avoids being blocked on upstream given the
free-threading interest. Revisit only if v1 grows toward rich automatic
marshalling. (`PyRef`/`Gil`/marshalling stay internal per Q3, so no public
abstraction is frozen either way.)

**Spike outcomes:**

- **abi3 limited-API audit -- RESOLVED (viable; floor = 3.12).** A real
  `Py_LIMITED_API=0x030c0000` extension exercising all four v1 mechanisms
  (`PyType_FromSpec` + a C struct embedded after `PyObject_HEAD`,
  `PyErr_NewException`, `PyObject_GetBuffer` consumption, `PyModuleDef`/
  `PyInit_`) compiled, linked, imported under CPython 3.12.3, and ran -- pure
  stable-ABI symbols (`nm -D` confirmed). **3.11 is the hard floor** (the
  `Py_buffer` struct only enters the limited API in 3.11); **3.12 is the
  committed target**. The **pre-PEP-697 embedded layout works**, so v1 needs
  no `PyObject_GetTypeData`/3.12-only machinery.
- **Panic interceptability -- RESOLVED (needs a small runtime change).**
  `tpy_panic` is `std::exit(1)` (`runtime/cpp/include/tpy/core.hpp`) --
  uncatchable, so no boundary `try/catch` can convert it as-is. Fix: a
  macro-gated **throwing `TpyPanic`** (deliberately *not* `BaseException`-
  derived) compiled only for `.so` builds; default builds keep `std::exit(1)`
  (test/abort model unchanged). Effort **S**, plus an audit of every
  `catch (...)` in the runtime/codegen so none absorbs a panic in `.so` mode.
  (`raise<E>()` already throws a catchable `BaseException` -- the ordinary-
  exception bridge, separate from the panic path.)
- **Ephemeral-borrow soundness -- RESOLVED (insufficient; zero-copy
  deferred).** A foreign-buffer `Span` param **cannot** be soundly fenced in
  v1 by extending `ephemeral_borrow_vars`. The escape machinery is *built on*
  the invariant "a parameter names caller-owned storage that outlives the
  call" (`_name_is_param_or_global`); a foreign buffer dies at *call end*,
  inverting it -- and **0/8 escape sinks reject a foreign-borrow param today**
  (return/yield/closure actively classify it *safe*: a silent UAF, not a
  conservative reject). Sound zero-copy needs the **full foreign-borrow
  primitive** -- an interprocedural "callee must not stash this borrow"
  contract (pass-to-unknown-fn sink) + frame-promotion handling (generator/
  async sink), both IR-gated. **Consequence: v1.0 buffer input is copy-in;
  zero-copy buffer input is the first post-v1.0 work** (phase 3.5, with the
  primitive). The field/global/container sinks overlap a pre-existing
  `BUGS.md` view-lifetime gap and are wanted regardless.

## Decisions locked

- Panic at the boundary -> **converted to a Python exception**, not abort.
- Exposure is **explicit opt-in**, never implicit -- by *extending* the
  existing `@export` / `tpy.extern.export` linkage, not a new decorator.
- `PyRef`/`PyCallable` stay **internal-only in v1** (not in user-facing
  signatures); user-facing `PyRef` ships with `nogil` GIL checking, together.
- **v1.0 buffer input is copy-in.** A spike showed the ephemeral-borrow
  interim is insufficient (a foreign `Span` param can't be fenced at all
  escape sinks); zero-copy buffer input is the **first post-v1.0 work**, with
  the foreign-borrow primitive (phase 3.5). `str`/`bytes` args are copy-in too.
- **Panic -> exception needs a runtime change** (spike): a macro-gated
  throwing `TpyPanic` for `.so` builds (default builds unchanged) + a
  `catch (...)` audit. Effort S.
- GIL capability is **ambient (flow-fact)**, not an explicit token param.
- **abi3 / limited API: committed, floor = 3.12** (spike-validated; 3.11 hard
  minimum). Glue is **hand-rolled raw C-API**, not nanobind.
- Async is **out of scope for v1**, hooks reserved.

## Resolved design questions

- **Q1 user-exception -> Python-class generation -- RESOLVED (faithful).**
  At `PyInit`, generate one Python exception class per user TPy exception
  type (`PyErr_NewException`), preserving the inheritance chain (TPy
  `Exception` -> Python `Exception`; `class ParseError(ValueError)` ->
  `__bases__ = (ValueError,)`), registered as module attributes; data fields
  map to instance attributes (+ `args`). Built-ins map to their Python
  counterparts. A runtime registry (TPy exc type -> `PyObject*` class) drives
  `PyErr_SetObject` from the boundary `catch` (and from the `@error_return`
  `Err` check). Generate for **all user exception types reachable in the
  extension** (thrown types aren't tracked per function; generation is cheap).
  Deferred: `__cause__`/`__context__` chaining, custom `__str__`.
- **Q2 returning a `Span`/buffer -- RESOLVED (reframe + defer).** A bare
  `-> Span[T]` can't own its backing, so the right shape is **an exposed
  class that exports a buffer** (`bf_getbuffer`/`bf_releasebuffer` over its
  contiguous storage, tied to `__span__`/`Spannable`); the PyObject is the
  exporter that keeps memory alive. Folded into the exposed-classes work,
  deferred until numeric-*output* demands it. v1 returns owned copies.
- **Q3 `PyRef` surface -- RESOLVED (internal-only, locked).** `Own[PyRef]`/
  `PyCallable` are library types but **not exposable in user-facing
  signatures in v1**. This is the invariant that keeps ambient-always GIL
  sound. User-facing `PyRef` (opaque passthrough / callbacks) arrives in the
  *same* later phase as real `nogil` GIL checking, not piecemeal.
- **Q4 exposed-class dunders -- RESOLVED (all four groups in scope).** Past
  the baseline (`__init__` + methods + getset properties): `__repr__`/`__str__`,
  `__eq__`/`__hash__`, ordering + arithmetic operators (rich-compare + `nb_*`),
  and the container protocol (`__len__`/`__getitem__`/`__setitem__`/`__iter__`).
  Suggested landing order by cost/dependency -- repr/str + eq/hash first
  (cheap, universal), then operators, then container -- but all four are
  in-scope for the classes work. Inheritance of exposed class *hierarchies*
  stays deferred (each exposed class is flat in v1); `@dynamic` protocols are
  not exposed as Python ABCs.
- **Q7 `PyRef` through `readonly` -- RESOLVED (no `interior[]` hatch).** Unlike
  `Rc` (whose refcount cell is a TPy field), `PyRef`'s refcount lives in the
  foreign CPython object and is bumped via `Py_INCREF(ptr)` through the raw
  pointer -- outside TPy field-mutation tracking -- so `clone()` is a
  `@readonly` method needing no `interior[]`. Largely moot in v1 (Q3). Phase-1
  check: passing a `readonly[PyRef]`'s ptr to a mutating native fn must be
  allowed (operates on the opaque pointee).

## Still open

- **Q5 GIL diagnostic quality** -- definition-of-done for the (later) `nogil`
  phase: released-here span + offending-op span + `with gil:` help + the
  specific reason (which op / which `PyRef`). Concrete message specs when that
  phase lands; nothing to decide before then.
- **Q6 foreign-borrow primitive** -- the spike resolved the *mechanism*
  question: the ephemeral interim is insufficient, and the full primitive is
  required for *any* zero-copy input (str args **and** buffer input). Still
  open is only its *timing* -- it is the first post-v1.0 priority (phase 3.5),
  IR-gated (MIR `Place`/`LoanInfo`; shared with the `@native -> V`
  borrow-contract gap).

## Suggested phasing

Detail for the tracker table in "v1 plan and status" (top). **v1.0 = phases
1-3** (free functions + buffers); **v1.1 = phases 4-5** (classes, enums);
3.5 / 6 / 7 / 8 are beyond v1.

1. **Marshalling layer + cpython facade (abi3).** The shared `to_py` /
   `from_py` for primitives (`str`/`bytes` **copy-in**, not borrow -- see
   scope refinement) + the `tpy/interop/cpython_h.hpp` facade and
   `lib/tpy/_bindings/cpython.py`. `Own[PyRef]`/`PyCallable` as `Rc`-pattern
   library types over a tiny C++ facade. Route every op through the (ambient,
   always-present) `Gil` capability. The keystone; unlocks everything.
2. **Extension codegen + free functions + local `.so`.** `ext_module`
   directive; *extend* the `@export` linkage; a sibling
   `generate_extension_glue()` (mirroring `generate_fwd_header`) for the glue
   TU (`PyMethodDef`/`PyModuleDef`/`PyInit_`); the **`.so` build-output mode**
   (`-shared`, Python include dir, allow-undefined symbols on Linux/macOS,
   `pythonXX.lib` on Windows, no `main` -- reuse `--no-main`); the
   exception+panic bridge. Tested hard via a local `.so` + driver parity (no
   packaging yet). End-to-end "import a TPy function from CPython."
2.5. **PEP 517 backend -> abi3 wheel.** Wrap the proven local `.so` build in
   the `turbopython.build` backend so `uv build` / `pip wheel` produce the
   abi3 wheel. Decoupled from phase 2 so packaging complexity does not gate
   interop correctness (per the co-validate review).
3. **Buffer input (`Span`), copy-in.** `PyObject_GetBuffer` -> read the bytes
   -> copy into an owned TPy `array`/`list` -> `PyBuffer_Release`, with dtype/
   format checks. Numeric input (numpy/`memoryview`) works but is **copied in**
   -- the ephemeral-borrow spike showed a zero-copy foreign `Span` cannot be
   made sound in v1 (see spike outcomes). Zero-copy is phase 3.5.
3.5. **(IR-gated) Foreign-borrow primitive -- the first post-v1.0 work.**
   Delivers *all* zero-copy input: non-escaping foreign `Span` buffers **and**
   promote-on-escape str args. The spike showed this is required even for
   read-only buffer input (the ephemeral interim can't fence a foreign param).
   Shared with the `@native -> V` borrow-contract gap; lands against the MIR
   `Place`/`LoanInfo` model, not the string-key `BorrowTracker`.
4. **Classes + methods.** `PyType_FromSpec` heap types, `tp_init`/
   `tp_dealloc` over the embedded TPy struct, methods + getset properties.
   **Baseline implemented**: `@export class` -> a CPython type whose instance
   embeds the TPy payload after the `PyObject` header (`Instance<T>`); `tp_init`
   runs `__init__`, `tp_dealloc` runs the C++ destructor; plain instance methods
   + annotated fields as read/write getset; instances cross as free-fn/method
   params (a borrow of the live payload -- mutation writes through) and returns
   (copy/move into a fresh instance via `instance_to_py`, so a borrow-form
   `-> Cls` return warns; `Own[Cls]` is the acknowledged form). The type is
   final (no `BASETYPE`) with no instance `__dict__`. Deferred: dunders (Q4),
   inheritance of exposed hierarchies, `@property`, class-typed fields,
   static/classmethods. Folds in faithful exception data-field crossing
   (exc TODO 1a -- same per-instance field marshalling).
5. **Enums + constants.** Module attributes; `IntEnum` for int-backed enums.
6. **Containers (by-copy, declared divergence).** `list`/`dict`/`set`/
   `tuple` <-> PyList/PyDict/... with the no-alias divergence documented.
7. **(Later) `nogil` / `with gil`.** Add capability *removal* + escape
   check + diagnostics; parallelism. Purely additive over phase 1.
8. **(Later) Embedding, callbacks/opaque PyRef, async <-> asyncio** behind
   the reserved hooks.
