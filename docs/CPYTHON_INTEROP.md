# CPython Interop -- Design

**Status: exploratory.** Captures a design discussion, not a committed
spec. No code exists yet. The goal is to agree the shape before any
`/tpy-add-feature` pass. Companion to `PROJECT_TOOLING_DESIGN.md`, which
reserves the *tooling* hooks (the `ext` target kind + TPy as a PEP 517
build backend); this doc is about the *interop semantics* that doc
deliberately defers.

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
functions, and zero-copy read-only numeric buffer input. **v1.1 = phases
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
| 1 | Marshalling layer + cpython facade (abi3) -- the keystone | **v1.0** | 🔬 |
| 2 | Extension codegen; **free functions** end-to-end; local `.so` build | **v1.0** | 🔬 |
| 2.5 | PEP 517 backend -> abi3 wheel (packaging) | **v1.0** | 🔬 |
| 3 | Buffer protocol -- zero-copy read-only input (ephemeral borrow) | **v1.0** | 🔬 |
| 4 | **Classes + methods** (`PyType_FromSpec`; dunders per Q4) | **v1.1** | 🔬 |
| 5 | **Enums + constants** | **v1.1** | 🔬 |
| 3.5 | Full foreign-borrow primitive (promote-on-escape; str/stored buffers) | later -- IR-gated | 🔬 |
| 6 | Containers (`list`/`dict`/`set`/`tuple`, by-copy) | v1-adjacent | 🔬 |
| 7 | `nogil` / `with gil` (parallelism + GIL checking) | later | 🔬 |
| 8 | Embedding, callbacks / opaque `PyRef`, async <-> `asyncio` | later | 🔬 |

The keystone is **phase 1** (marshalling layer + facade); it unlocks
everything else and is the natural `/tpy-add-feature` entry point.

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
is also the "make this kernel fast" case that matters for the HFT
audience.

**Perf note (HFT angle).** The speed win is the buffer/numeric path, not
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
| `str` / StrView | PyUnicode | O(n) out; **copy-in (v1)**, borrow later | see "str/borrow" |
| `bytes` / BytesView | PyBytes | O(n) out; copy-in (v1), borrow later | see "str/borrow" |
| `Span[T]` numeric | buffer / `memoryview` / ndarray | **O(1) zero-copy** (read-only, non-escaping) | see "buffer protocol" |
| `list`/`dict`/`set`/`tuple` | PyList / PyDict / ... | **O(n)*elem, by-copy** | see "container cliff" |
| record / class | extension-type wrapper | O(1) pointer | see "classes" |
| `Optional[T]` / None | None | O(1) | |
| `A \| B` union | tag dispatch | varies | |

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

**v1 default: copy-in.** For `str`/`bytes` args, v1 *copies on entry* (always
sound); the zero-copy borrow is the deferred optimization that lands with the
foreign-borrow primitive (phase 3.5). This section describes the end state,
not the v1 behavior.

### The buffer protocol -- the high-value zero-copy path

`Span[readonly[T]]` (and writable `Span[T]`) bind to any object exposing
the buffer protocol -- notably numpy arrays and `memoryview`:

- `from_py` calls `PyObject_GetBuffer` (request `PyBUF_SIMPLE` +
  format/itemsize check; `PyBUF_WRITABLE` for a mutable `Span[T]`),
  yielding a `Py_buffer` whose `.buf` + `.len` become the `Span` view.
  `PyBuffer_Release` runs after the call.
- **Soundness under GIL release:** `PyObject_GetBuffer` (called at call
  entry, GIL held) takes a reference that keeps the exporter alive until
  `PyBuffer_Release`. So the borrowed memory stays valid for the whole
  call even if the GIL is released inside it (see "GIL"). This is the same
  assumption Cython/PyO3 rely on.
- **v1 mechanism (ephemeral borrow):** the input `Span` is *non-escaping* --
  readable in the function but not storable past the call -- which makes the
  borrow sound *without* the full foreign-borrow primitive (see Q6). This
  zero-copy read-only buffer input ships in v1 (phase 3); writable/escaping
  views wait for phase 3.5.
- **dtype/format match:** TPy element type <-> buffer format code
  (`float` <-> `'d'`, etc.); mismatch is a marshalling error raised as a
  Python exception.

**Resolved (Q2):** *returning* a `Span` to Python is harder (TPy would
have to export its own buffer or copy). v1 accepts buffers as input and
returns owned copies / arrays; TPy-as-buffer-exporter is deferred.

### The container cliff (a declared divergence)

`list[int]` <-> PyList is a full O(n) marshal that produces a **copy** --
the two sides do **not** alias, so mutations do not propagate. This is a
CPython-parity divergence, but an *inherent, declarable* one (the project
rule: declared divergence is acceptable, silent is not). The doc states it
plainly and steers bulk/numeric users to `Span` + buffer protocol (which
*does* alias, zero-copy).

We do **not** build alias-preserving lazy proxies in v1 (a PyObject
wrapping the vector, boxing per `__getitem__`) -- high complexity, and it
fights the unboxing that is the whole point.

## abi3 / limited API commitment

v1 targets the **limited API / stable ABI**, for two reasons that
compound:

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
`tp_new` runs the TPy constructor, `tp_dealloc` runs the C++ destructor (so
`Own`/`Box`/`Rc`/container fields clean up correctly). Payoff: because the
PyObject **owns** the instance, multiple Python references alias and see
mutations -- exposed classes get *correct Python reference semantics*,
unlike containers. A method call unwraps `self` to a borrow valid for the
call (same borrow story as `str`).
*Scope dial:* baseline = `__init__` + plain methods + annotated fields as
getset properties; then all four dunder groups (`__repr__`/`__str__`,
`__eq__`/`__hash__`, ordering + arithmetic operators, container protocol --
see Q4), landed roughly in that order. **Defer** inheritance of exposed
class *hierarchies* (each exposed class is flat in v1).

**3 + 4. Enums + constants** -- mostly bookkeeping at `PyInit`. Constants
become module attributes. *Scope dial on enums:* int-backed enums expose as
`IntEnum` or plain module constants (easy). Data-carrying (algebraic) enum
variants, if present, need a wrapper class per variant and are **deferred**;
v1 ships simple/int enums.

**Feasibility verdict:** all four are feasible. The dominant cost is the
shared marshalling layer and the extension-type generator; the four kinds
layer cheaply on top. Substantial (multi-week), not a research problem; the
scope dials above are how size is controlled.

## Exception and panic bridge (load-bearing even for free functions)

TPy has two runtime failure channels; both must bridge to CPython's
thread-state error model.

- **Exceptions (`raise` / `except`).** A TPy `raise` that reaches the
  boundary becomes `PyErr_SetObject` and the wrapper returns the C-API
  error sentinel (`NULL` / `-1`). Built-in TPy exception types map directly
  to their Python counterparts (`IndexError`, `ValueError`, ...). User
  exception types get a generated Python class per type at `PyInit`
  (`PyErr_NewException`), preserving the inheritance chain and mapping data
  fields to instance attributes; a runtime registry (TPy exc type ->
  `PyObject*` class) drives `PyErr_SetObject` from the boundary `catch`. See
  "Resolved design questions" Q1 for the full mechanism.
- **`@error_return`** functions exposed across the boundary: the `Err`
  branch surfaces as a *raised* Python exception (the natural Python idiom),
  the `Ok` branch as the unwrapped value.
- **Panic (`tpy_panic`).** A panic would otherwise `abort()` the *host
  interpreter* -- hostile. **Decision: at the CPython boundary a panic is
  converted to a Python exception** (`SystemError`, or a dedicated
  `TpyPanic`), not an abort. This bends the "panic = unrecoverable" model,
  but aborting the host is unacceptable. The conversion happens only at the
  boundary wrapper; intra-TPy panic semantics are unchanged. **Caveat (early
  spike):** this assumes `tpy_panic` is *interceptable* at the boundary. If it
  is a hard `abort()`/`terminate` today, conversion needs panic routed through
  a catchable mechanism at the extension boundary -- a runtime change, not a
  wrapper detail.

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
  model (the `gil_held`/`nogil` design) fundamentally and need separate wheels
  (the stable ABI does not yet cover free-threaded builds). Keep the GIL
  capability abstract enough that a free-threaded target can map it to
  per-object locking / "always parallel" rather than a single global lock.
  Relevant to the HFT parallelism angle; revisit when `nogil` (phase 7) is
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

The *full* zero-copy-borrow generality (promote-on-escape; str args and
*stored* buffers) is gated on the foreign-borrow primitive (ultimately the
IR/region work). So v1 **copies `str`/`bytes` args on entry** (always sound;
matches the container-copy story). The one in-scope exception is **read-only
numeric buffer input** (`Span[readonly[T]]`): it ships zero-copy in v1 via the
ephemeral (non-escaping) borrow -- but *only contingent on* a proven
escape-rejection rule covering **return, field/global store, container insert,
closure/generator/async capture, and calls to unknown functions**. If that
rule can't be made airtight in phase-3 design, buffer input falls back to
copy-in as well. Either way, phases 1-2 stay free of the IR dependency.

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
- **Codegen (isolated).** A dedicated `tpyc/codegen_cpp/extension.py` (or a
  small `codegen_cpp/cpython/` package) owns glue-TU emission: per-function
  `from_py`/`to_py` wrappers, `PyMethodDef`/`PyModuleDef`/`PyInit_`,
  `PyType_FromSpec` for classes, exception-registry init. Invoked as a sibling
  `generate_extension_glue()` (the `generate_fwd_header` pattern), so the
  load-bearing `(hpp, cpp)` codegen contract is untouched.
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

### Testing (normal `tests/cases/` harness, new case kind)

Interop tests ride the existing snapshot harness with one exec variant, and
keep the **same TPy-vs-CPython parity check** the `cpy` phase already gives --
the right safety net for interop:

- **Layout.** An `ext_module` `src/main.py` (compiled) + a plain Python
  **`driver.py`** that imports and exercises it
  (`assert main.dot(a, b) == ...; print(...)`). The driver is *identical*
  across both runs below; only what `main` resolves to changes.
- **comp** (unchanged shape): snapshot the generated `.hpp`/`.cpp` **and the
  glue TU**; validate diagnostics via `# tpyc:` annotations.
- **ext-exec** (new variant): build the `.so` (the new build-output mode), run
  the driver under CPython with the built module importable, compare stdout to
  `output.txt`.
- **cpy parity**: run the *same* driver with `PYTHONPATH=lib/cpy:src_dir` so
  `import main` loads the TPy **source** interpreted by CPython; compare the
  same `output.txt`. The compiled extension must behave like its
  source-as-Python. Requires a `lib/cpy/tpy/interop.py` stub (`@export` =
  identity, `ext_module` ignored; buffer-protocol `Span` -> numpy/`memoryview`,
  which already work under CPython).

This driver-based parity is also the reference-vs-extension shape that
satisfies the project's reference-type test-adequacy rule (mutate-and-observe
across the boundary). Env: building the `.so` needs `Python.h` (available --
tests run under CPython) + the toolchain; fingerprint/skip logic extends to the
ext build.

## Alternatives considered, and spikes to run first

**Alternative: emit nanobind (or pybind11) glue instead of hand-rolling the
C-API.** nanobind (header-only, modern, lighter than pybind11) already
provides most of phases 2-7: function/class/enum binding, exception
translation, the buffer protocol, GIL scope guards, and first-class abi3 +
free-threading support. Since TPy generates C++ anyway, it could emit nanobind
calls rather than raw `PyMethodDef`/`PyType_FromSpec`/`PyErr_*`. Trade-off:
hand-rolled gives full control, minimal/auditable output, and avoids a heavy
template dependency that adds to C++ compile time (already the
back-end-dominated cost) -- consistent with the "tiny C++ helpers" aesthetic;
nanobind would massively cut the glue surface and inherit its abi3/
free-threading maturity. **Not yet decided -- spike both on the free-function
slice and compare** (glue size, compile time, control, abi3/free-threading
coverage) before committing the hand-rolled path.

**Spikes to run before locking v1 scope:**

- **Limited-API audit (abi3).** Prove every required API is available under
  `Py_LIMITED_API`: `PyType_FromSpec` slot coverage, embedding a C++ object
  after the instance header (opaque-header layout; PEP 697 `PyObject_GetTypeData`
  is 3.12+), buffer-export slots (`Py_bf_getbuffer`, accepted by
  `PyType_FromSpec` only in 3.11+), exception-class creation, and module
  state. **Pick the abi3 floor** (e.g. 3.12 for the cleanest story) -- it
  gates which mechanisms are available, and may overturn the abi3 commitment
  vs the nanobind option above.
- **Panic interceptability.** Confirm whether `tpy_panic` can be caught and
  converted at the boundary, or whether the panic path needs a runtime change
  first (see "Exception and panic bridge").
- **Ephemeral-borrow soundness.** Prove a foreign param `Span` borrow is
  rejected at *every* escape sink (return, field/global store, container
  insert, closure/generator/async capture, unknown-fn calls) -- the
  precondition for zero-copy buffer input shipping in v1.0; otherwise it falls
  back to copy-in.

## Decisions locked

- Panic at the boundary -> **converted to a Python exception**, not abort.
- Exposure is **explicit opt-in**, never implicit -- by *extending* the
  existing `@export` / `tpy.extern.export` linkage, not a new decorator.
- `PyRef`/`PyCallable` stay **internal-only in v1** (not in user-facing
  signatures); user-facing `PyRef` ships with `nogil` GIL checking, together.
- Zero-copy buffer input ships v1 via the **ephemeral-borrow interim**
  (non-escaping `Span`); the full promote-on-escape primitive is deferred/
  IR-gated. `str`/`bytes` args are copy-in in v1.
- GIL capability is **ambient (flow-fact)**, not an explicit token param.
- abi3 / limited API is the **v1 target, pending a limited-API audit** (a
  spike must prove every required API is available under `Py_LIMITED_API` --
  see "Alternatives & spikes"); not yet a hard lock.
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
- **Q6 full foreign-borrow primitive timing** -- the *promote-on-escape*
  generality (str args and buffers that may be *stored*) is IR-gated (MIR
  `Place`/`LoanInfo`; shared with the `@native -> V` borrow-contract gap) and
  co-designed with that work. v1's zero-copy *read-only* buffer input does
  **not** wait on it -- it ships via the ephemeral-borrow interim (phase 3).

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
3. **Buffer protocol (`Span`), zero-copy read-only input.** `PyObject_GetBuffer`
   -> `ptr+len` -> `Span(ptr, len)` (construction already exists/tested), with
   dtype/format checks + `PyBuffer_Release` discipline. Zero-copy via the
   **ephemeral-borrow interim**: the `Span` is forbidden from escaping the
   function (extends `ephemeral_borrow_vars`), so the `Py_buffer` keeping the
   memory alive for the call is sufficient -- no full foreign-borrow primitive
   needed. (Validate the ephemeral extension during this phase's design.)
   `str`/`bytes` args stay **copy-in** until the full primitive (phase 3.5).
3.5. **(IR-gated, optional) Full foreign-borrow primitive.** Generalizes to
   *promote-on-escape* over a foreign source -- str args and buffers that may
   be *stored* past the call. Shared with the `@native -> V` borrow-contract
   gap; lands against the MIR `Place`/`LoanInfo` model, not the string-key
   `BorrowTracker`. Optimization, not a blocker.
4. **Classes + methods.** `PyType_FromSpec` heap types, `tp_new`/
   `tp_dealloc` over the embedded TPy struct, methods + getset properties.
5. **Enums + constants.** Module attributes; `IntEnum` for int-backed enums.
6. **Containers (by-copy, declared divergence).** `list`/`dict`/`set`/
   `tuple` <-> PyList/PyDict/... with the no-alias divergence documented.
7. **(Later) `nogil` / `with gil`.** Add capability *removal* + escape
   check + diagnostics; parallelism. Purely additive over phase 1.
8. **(Later) Embedding, callbacks/opaque PyRef, async <-> asyncio** behind
   the reserved hooks.
