# datetime stdlib design

Status: **v0 + v1 + v2 landed (timedelta, date, naive time/datetime + wall
clock); v3 pending.**
This document is the contract for the whole `datetime` track. It captures the
type model, storage/representation, operator strategy, timezone model, and the
phased build. Implementation lands rung-by-rung per the roadmap below; update
the Status column of the roadmap table as each phase merges.

The parity oracle is **CPython 3.12** (the repo's venv Python). The cpy test
phase resolves the real CPython `datetime` (no `lib/cpy/tpy/datetime.py`
stub), so our observable output -- `repr`, `str`, `isoformat`, error types --
is byte-compared against CPython directly, exactly like the `csv` module.

## Roadmap / progress

| Phase | Scope | Status |
|---|---|---|
| **v0** | `@overload`-operator codegen fix (skip the impl signature when emitting operators) + focused test. Prerequisite for all operand-polymorphic operators. Routed via `/tpy-fix-bug` (it is a defect). | **Done** (`_gen_binary_operators` overload-impl guard; test `operators/overload_binary_operator`) |
| **v1** | `timedelta` (integer-surface arithmetic: `+ - `, unary, `*int`, `//int`, `//td`, `/td`->float, `%td`, comparisons, `abs`, `total_seconds`, repr/str) and `date` (ctor+validation, attributes, `weekday`/`isoweekday`, `isoformat`/`str`, comparisons, `date +/- timedelta`, `date - date`, repr). Pure-TPy, **no native dependency**, no `today()`. Prerequisite compiler fixes: `abs()`->`__abs__` dispatch (P1), `/`-vs-`//` decoupling (P2). | **Done** (tests `stdlib/datetime_{timedelta,date}`; byte-parity with CPython) |
| **v1-deferred** | `timedelta` float/rounding surface: float constructor args (`timedelta(hours=1.5)`), `td / number` (round-half-to-even -> timedelta), float `*`/`/`. Compile-error (rejects-valid) until landed, not silent. (The `/`-overload result-typing bug that also blocked `td / number` is fixed.) | Deferred |
| **v2** | `datetime` and `time` (the `datetime.time` class), `now()`/`utcnow()`/`today()`/`fromtimestamp()`/`utcfromtimestamp()`/`combine()`, `date.today()`. Naive-only. Introduces the vendored Hinnant `date` backend behind the `stdlib/datetime.hpp` facade for the local-offset lookup. | **Done** (tests `stdlib/datetime_{time,datetime,now}`; byte-parity with CPython; `dt.date()`/`dt.time()` accessors excluded -- blocked on the member-name/type-name C++ collision bug in BUGS.md, follow-up once fixed) |
| **v3** | `strftime`/`strptime`/`fromisoformat` (pure-TPy directive engine) and fixed-offset `timezone` awareness (aware `datetime`, `astimezone`, offset-aware arithmetic/comparison). | Not started |
| **Deferred** | `fold`; `zoneinfo`/IANA DST (`ZoneInfo` value type backed by the tz db); user-defined `tzinfo` subclasses; Windows tz backend. Filed, not silent. | Deferred |

## Goal

Standard, idiomatic Python `datetime` that compiles and matches CPython's
observable behavior:

```python
from datetime import date, timedelta, datetime

d = date(2021, 3, 5)
print(d.isoformat())            # 2021-03-05
print((date(2021, 3, 8) - d))   # 3 days, 0:00:00   (date - date -> timedelta)
print(d + timedelta(days=10))   # 2021-03-15        (date + timedelta -> date)
print(date.today())             # local current date (v2)

td = timedelta(hours=25, minutes=30)
print(td)                       # 1 day, 1:30:00
print(td / timedelta(hours=1))  # 25.5              (delta / delta -> float)
print(repr(td))                 # datetime.timedelta(seconds=91800)
```

## Type model

All four core types are `@dataclass(frozen=True)` and implement `ValueType`:
they are **immutable value types** -- copied, not referenced -- which matches
CPython's immutable-value feel (identity is not observable for these types)
and makes them hashable and dict/set-key usable.

The decorator earns its keep for the mechanical dunders; the semantically
load-bearing ones are hand-written:

| Provided by `@dataclass` | Hand-written |
|---|---|
| `__eq__`, `__hash__` (field-based -- correct: two dates equal iff same y/m/d; hash *values* are not byte-compared) | `__init__` (validation + normalization) |
| `__lt__/__le__/__gt__/__ge__` via `order=True` on `date` / `timedelta` only (fields are declared in significance order, so tuple comparison *is* the correct chronological / duration order, including normalized negative `timedelta`) | `__repr__`, `__str__` (CPython-exact -- see Formatting) |
| value-type / immutability / frozen storage | the operators (`__add__`, `__sub__`, `__mul__`, ...) |

`datetime` does **not** use `order=True`: once `tzinfo` is a field, a
field-tuple comparison is wrong (aware comparison converts to UTC;
naive-vs-aware raises). `datetime` comparisons are hand-written -- trivial
for naive (v2), tz-aware logic added in v3.

Public fields carry CPython's attribute names (`year`, `month`, `day`,
`hour`, `minute`, `second`, `microsecond` on date/datetime; `days`,
`seconds`, `microseconds` on timedelta), so `d.year` etc. read directly off
the frozen fields.

## Storage and representation

- **Constructor params are `int` (BigInt)**, matching CPython's signature.
  This is required, not stylistic: `timedelta` legitimately accepts
  components far beyond Int32 (`timedelta(seconds=10**10)`,
  `timedelta(microseconds=10**18)` are valid, normalizing into days), and
  the normalization intermediate (`days * 86400 * 10**6` for days near the
  10**9 legal max) exceeds Int64. CPython's own pure-Python `datetime.py`
  normalizes with arbitrary-precision `int` for exactly this reason; we port
  that.
- **Field storage is Int32.** Date/time components are naturally bounded
  (year 1..9999, month 1..12, hour 0..23, ...); `timedelta` stores the
  normalized triple `(days, seconds, microseconds)` with
  `days in [-999999999, 999999999]`, `seconds in [0, 86399]`,
  `microseconds in [0, 999999]` -- all Int32-sized, matching CPython's
  attributes exactly.
- **Validate-and-normalize in BigInt, before the store.** The constructor
  checks ranges (and normalizes `timedelta`) while values are still BigInt,
  raising the appropriate exception, *then* assigns to the Int32 fields.
  Because validation bounds the value first, the implicit BigInt->Int32
  narrowing can never hit its overflow panic.

## Errors

Construction raises **catchable** exceptions matching CPython's *type*:
`ValueError` for out-of-range components (`date(2021, 13, 1)`,
`date(2021, 2, 30)`, `hour=25`, year outside 1..9999) and `OverflowError` for
a `timedelta` beyond +/-10**9 days. We match the exception *type*, not the
exact message text; tests print the type (or a stable token), not `str(e)`.

## Operators and the v0 codegen fix

`datetime` arithmetic is pervasively **operand-polymorphic** -- one operator
returns different types depending on the operand:

```python
date  - date       -> timedelta
date  - timedelta  -> date
timedelta / int        -> timedelta   # DEFERRED (v1-deferred: float/rounding surface)
timedelta / timedelta  -> float
timedelta // int       -> timedelta
timedelta // timedelta -> int
```

(`timedelta / int` -- round-half-to-even -> timedelta -- is v1-deferred with
the float surface; v1 ships `timedelta / timedelta -> float` only. The
`/`-overload result-typing bug that also blocked it is fixed.)

Python has one `__sub__`, so this is expressed with `typing.overload`. TPy
supports two spellings: typed stubs plus one shared implementation that
dispatches via `isinstance` (the CPython-source shape), or individually
implemented overloads, each with its own body (the C++-overload shape --
`functools.reduce` precedent). The module uses the individually-implemented
form: the isinstance dispatch was pure ceremony since the compiler
specializes per operand type anyway. Sema resolves and narrows the result
type by operand either way.

Note on the parity model: `lib/tpy/datetime.py` is compiled **only by TPy** --
the cpy phase resolves the real CPython `datetime` (see the Status note), so
our implementation file is never executed by CPython (same as `csv`). It
therefore only has to compile+run under TPy; parity is verified at the
*user-code* level, where each toolchain uses its own `datetime`. This is why
value-type spellings TPy accepts but CPython would reject at runtime -- e.g. a
`@dataclass(frozen=True)` with a custom `__init__` that assigns `self.field =
...` (TPy allows field assignment inside `__init__`; CPython's frozen
`__setattr__` would raise) -- are fine here. The source stays syntactically
valid Python for tooling, but need not be *runnable* under CPython.

**v0 fix.** Codegen currently mis-emits this pattern. For an `@overload`
operator set it correctly specializes the shared body into one concrete C++
operator per typed overload, but *also* emits an extra friend operator for
the **implementation method's own signature** -- whose parameter is the
union (`Day | Delta`) purely because that is how the shared body is spelled.
That extra operator forwards to a `__sub__(variant)` method that was never
generated (the impl is specialized away), so it dangles and the TU fails to
compile -- meaning *any* `@overload` operator with a shared impl currently
fails to build even with purely concrete operands. The fix: when emitting
operators for an `@overload` set, **skip the implementation signature**;
emit operators only for the typed overload signatures. No `std::visit`, no
sema change.

Union-typed *operands* (`x - u` where `u: date | timedelta`) stay
unsupported. This is a **separate, pre-existing defect** -- sema accepts a
union operand even for a plain monomorphic operator (`a + u`), and C++ then
cannot build it -- so it is filed independently in `BUGS.md` and kept out of
datetime's path, not introduced by this work.

See `docs/OVERLOAD_DESIGN.md` for the existing overload-resolution design.

## Timezone model (chrono-inspired)

CPython's `tzinfo` is an open abstract base: a datetime may carry any user
subclass, which implies runtime polymorphism (a reference type). That fights
the value-type model. Rust's ecosystem shows the way out -- neither `chrono`
(`DateTime<Tz>`, tz as a monomorphized type parameter) nor the `time` crate
(`OffsetDateTime` with a value `UtcOffset`) uses runtime polymorphism; even
full IANA/DST support (`chrono_tz::Tz`) is a concrete `Copy` value carrying a
zone id into static tables.

We take the underlying idea (not the generic spelling, which would break
CPython's non-generic `datetime`): the tz is a **closed set of value-typed
kinds** stored inside a single `datetime`, never an open subclassable base.

- v1/v2: naive only (`tzinfo=None`).
- v3: fixed-offset `timezone` (a small frozen value: offset `timedelta` +
  optional name). Stored as `Optional[timezone]`; `optional<value-type>` is
  still a value type, so `datetime` stays a value type end-to-end.
- Deferred: a value-typed `ZoneInfo` (interned zone id -> static DST tables,
  backed by the tz db) as a closed-union widening
  (`Optional[timezone | ZoneInfo]`).

**Permanent divergence:** user-defined `tzinfo` subclasses are unsupported
(chrono gives this up too). Almost all real code uses `timezone.utc` or
`ZoneInfo("...")`, not a hand-rolled subclass.

## Timezone / local-time backend

The calendar arithmetic (ordinals, leap years, civil-from-epoch) and all
formatting stay **pure-TPy** (parity is controlled by us). The one thing pure
TPy cannot do is read the OS's local UTC offset and the IANA zone rules.

- **Backend = vendored Howard Hinnant `date`** (`runtime/cpp/third_party/`
  + a hand-written `runtime/cpp/include/tpy/stdlib/datetime.hpp` facade),
  following the same vendoring pattern as PCRE2 (`re`) and the Mozilla CA
  store (`ssl`). `date` loads the tz db once and does fast in-memory
  lookups, avoiding `localtime_r`'s per-call global lock (a measured problem
  in prior work). It is also the reference implementation behind C++20
  `std::chrono`, so it aligns with an eventual standard-library migration.
- **The facade decouples the provider.** The TPy `datetime` module talks only
  to minimal facade primitives (`local_utc_offset(epoch)`, later
  `zone_offset(zone_id, epoch)`). The concrete C++ tz provider sits behind
  it, so it can be swapped without touching TPy stdlib or generated code.
- **Coupling note:** the binding import is module-level, so ANY datetime
  import links the tz backend (and `--date=none` rejects the whole module),
  including pure-calendar use (`timedelta`/`date` arithmetic) that needs no
  OS access. Accepted for v2; a finer-grained model (link a managed dep only
  when its native symbols are referenced) is filed in TODO.md (Build
  pipeline).
- **Platforms.** Linux/macOS use the OS tz db (`USE_OS_TZDB`), no download or
  bundling. Windows ships no IANA db, so any vendored provider needs bundled
  tz data + a Windows->IANA zone mapping. This is filed, not solved here, and
  is in any case gated on the separate matter that TPy generated code uses the
  GCC statement-expression extension (unsupported by MSVC), so Windows is not
  a current target. The likely long-term Windows answer is `std::chrono` via
  MSVC (its tz db is backed by the OS ICU, no bundling) -- a backend swap
  behind the facade, not a datetime redesign.

## Formatting (pure-TPy)

`isoformat`, `str`, `repr`, and (v3) `strftime`/`strptime`/`fromisoformat`
are implemented in TPy for exact CPython parity, not delegated to C
`strftime` (locale-sensitive and divergent). Int32 fields support format
specs, so `isoformat` is `f"{self.year:04d}-{self.month:02d}-{self.day:02d}"`
etc. `repr` is hand-written to match CPython exactly -- module-qualified and
zero-omitting where CPython omits:

```
repr(date(2021, 3, 5))              -> 'datetime.date(2021, 3, 5)'
repr(timedelta(days=1, seconds=30)) -> 'datetime.timedelta(days=1, seconds=30)'
str(date(2021, 3, 5))               -> '2021-03-05'
```

v3 `strftime`/`strptime` implement the directive set in TPy with hardcoded
C-locale (English) month/day tables. Locale-dependent directives (`%c`,
`%x`, `%X`) and tz directives (`%z`, `%Z`) are scoped when v3 is designed in
detail.

## CPython parity: acknowledged divergences

The design matches CPython except for the following, all signaled
(compile-time rejection or a documented restriction):

(`total_seconds()` / `timedelta / timedelta` float precision was a fourth,
silent, divergence -- TPy lowered `BigInt / BigInt` as double-rounded
`double(a)/double(b)` -- until int/int true division became correctly
rounded in the runtime; it now matches CPython for all magnitudes.)

- **No `datetime` subclass of `date`.** We compose rather than inherit (keeps
  the value-type story clean). Consequence: `isinstance(dt, date)` is `False`
  (CPython: `True`), and cross-type comparison/equality (`date == datetime`,
  `date < datetime`) is a **compile error** in TPy (the auto `__eq__`/`__lt__`
  take `self`-typed operands) where CPython returns `False` / raises a runtime
  `TypeError`. Rejected either way; benign.
- **User `tzinfo` subclasses unsupported** (see Timezone model).
- **Naive-vs-aware mixing** is rejected at compile time (distinct handling)
  rather than at runtime (v3).
- **Extreme-arg construction** raises the correct catchable exception because
  we validate on BigInt before the Int32 store (no divergence -- noted here
  because the naive store-then-check ordering *would* have panicked).
- **The `TZ` environment variable is not consulted** for local-time
  conversions (`now()`, `fromtimestamp()`, `date.today()`): the Hinnant
  backend's `current_zone()` reads `/etc/localtime` directly, while libc
  (and therefore CPython) honors `TZ`. Identical on hosts that don't set
  `TZ`; divergent for programs relying on a runtime `TZ` override. A
  TZ-aware resolution (IANA-name `TZ` values via `locate_zone`, POSIX rule
  strings via `ptz.h`) is a possible backend refinement behind the same
  facade. Tests are TZ-agnostic (invariant-only), so no committed output
  depends on it.

Behavioral notes (matching CPython, recorded because the "obvious" choice
differs): an out-of-range `fromtimestamp()`/`utcfromtimestamp()` raises
**ValueError** (CPython's "year N is out of range") when the timestamp fits
64-bit time_t, and **OverflowError** ("timestamp out of range") beyond it --
unlike ordinal overflow in `date`/`datetime` +/- `timedelta`, which raises
OverflowError in both CPython and TPy. Both checks run BEFORE the local
offset is applied (and before the Int64 narrowing at the native boundary,
which would otherwise panic): CPython rejects an out-of-range UTC instant
even when the historical LMT offset would shift it into year 1. Known
non-emulated nuance: glibc's `localtime` fails with OSError in an
intermediate band (roughly |t| in 1e17..9.2e18) where TPy raises
ValueError; CPython's exact type there is libc-dependent, so we pin the
two stable bands only. A missing/corrupt OS tz database degrades local
conversions to UTC (offset 0) instead of failing -- the same silent
fallback glibc gives CPython.

Independent of datetime: the pre-existing "union operand accepted by sema,
uncompilable in C++ for operators" defect (affects monomorphic operators too)
is filed in `BUGS.md`.

## Testing

Cases under `tests/cases/stdlib/` (naming per the group), CPython-parity via
the cpy phase (no `no_cpython.txt` -- real CPython `datetime` is the oracle):

- **v0:** a focused `tests/cases/` case exercising `@overload` operators with
  a shared impl and purely concrete operands (compiles + runs); a
  `# tpyc: type(...)` assertion that the return type narrows per operand.
- **v1:** `timedelta` construction/normalization (incl. large components,
  negative normalization), full arithmetic, division/floordiv/mod overloads,
  comparisons, `abs`, `total_seconds`, repr; `date` construction + validation
  (happy + `error_`/exception cases for bad components), attributes,
  `weekday`, `isoformat`/`str`/repr, `date +/- timedelta`, `date - date`.
- **v2:** `datetime`/`time` construction, `combine`, `fromtimestamp`,
  `isoformat`/`str`/repr; `now()`/`utcnow()`/`today()` tested by invariants
  (values are non-deterministic, so assert relationships, not literals).
- **v3:** `strftime`/`strptime`/`fromisoformat` round-trips and directive
  coverage; fixed-offset `timezone` arithmetic/comparison/`astimezone`.

Reference-type note does not apply (these are value types), but tests must
still avoid host-dependent output (local-time cases print comparisons /
relationships, never absolute local timestamps).

## File layout

- `lib/tpy/datetime.py` -- the module (single file; not a package).
- `lib/tpy/_bindings/hinnant_date.py` -- `@native` binding over the facade
  (declares the tz-backend dep via `# tpy: link("date", managed=True)`).
- `runtime/cpp/include/tpy/stdlib/datetime.hpp` -- hand-written facade over
  the tz backend (v2+).
- `runtime/cpp/src/stdlib/date_shim.cpp` -- facade implementation; the only
  TU that includes the vendored headers (compiled by the lib's build
  factory, not the always-linked runtime source discovery).
- `runtime/cpp/third_party/date/` + `date.vendor.json` + `date.sources.txt`
  + `scripts/vendor_date.py` -- vendored Hinnant `date` (v2+).
- `tpyc/build/date.py` -- build wiring (sources, flags, CMake vars,
  `--date` mode flag).
- Tests under `tests/cases/stdlib/`.

Docs to keep in sync as phases land: `docs/STDLIB_ROADMAP.md` (the datetime
row + section), `docs/LANGUAGE_FEATURES.md` (if the v0 fix changes documented
`@overload` behavior).
