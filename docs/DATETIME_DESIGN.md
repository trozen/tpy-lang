# datetime stdlib design

Status: **v0 + v1 + v2 + v3 landed (timedelta, date, time, datetime with
fixed-offset timezone awareness, strftime/strptime/fromisoformat,
timestamp/astimezone, TZ-honoring backend).**
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
| **v2** | `datetime` and `time` (the `datetime.time` class), `now()`/`utcnow()`/`today()`/`fromtimestamp()`/`utcfromtimestamp()`/`combine()`, `date.today()`. Naive-only. Introduces the vendored Hinnant `date` backend behind the `stdlib/datetime.hpp` facade for the local-offset lookup. | **Done** (tests `stdlib/datetime_{time,datetime,now}`; byte-parity with CPython; `dt.date()`/`dt.time()` accessors added once the member-name/type-name C++ collision was fixed -- test `stdlib/datetime_accessors`) |
| **v3** | `strftime`/`strptime`/`fromisoformat` (pure-TPy directive engine), fixed-offset `timezone` awareness (aware `datetime`, `astimezone`, offset-aware arithmetic/comparison/hash, runtime naive/aware mixing rules), `timestamp()` via the CPython `_mktime` iterative local-inverse, `replace()`, `isoformat(timespec=)`, `now`/`fromtimestamp`/`combine` tz params, TZ-env honoring in the backend + `local_zone_abbrev` facade primitive, `time.tzset()` no-op. | **Done** (tests `stdlib/datetime_{timezone,strftime,strptime,fromisoformat,timestamp,tzenv_posix,time_fromiso_offset}` + `error_datetime_timezone_utc_attr`; byte-parity with CPython incl. DST gap/fold timestamps under pinned TZ) |
| **v4** | `zoneinfo.ZoneInfo` (IANA zones, per-instant DST offsets; value type = one interned zone id, equal-by-key; `lib/tpy/zoneinfo.py` re-exports the CPython import surface, the class lives in datetime.py because of the signature cycle) + PEP 495 `fold` (ctor/replace params, `.fold`, gap/fold offset selection, fold-honoring `timestamp()`, fromtimestamp/astimezone auto-fold on the second pass of a repeated wall time, CPython's same-tzinfo identity rule as same-zone-id: wall-field compare/subtract ignoring fold, hash always fold=0-normalized). tz slot = the closed value union `timezone \| ZoneInfo \| None`; facade grows `zone_lookup`/`zone_key`/`zone_wall_{offset,dst}_seconds`/`zone_wall_abbrev`/`zone_utc_offset_seconds` (DST derived from neighbor-interval offsets -- the OS tzfile only carries an is_dst flag). Prerequisite compiler fix: `= None` default on a 3+-arm value union emitted `nullptr`. | **Done** (tests `stdlib/datetime_zoneinfo_{basic,fold,convert,arith,errors}`; byte-parity with CPython incl. both fold values at the gap and fold wall instants) |
| **v4.1** | `zoneinfo.available_timezones()` (facade grows `zone_db_count`/`zone_db_key_at` over the provider's zone database, so every listed key constructs; CPython's placeholder entries `Factory`/`localtime` are excluded -- declared) + `ZoneInfo.fromutc` (reuses `_from_epoch_us`, fold set on the second pass). Declared construction-side divergence: `ZoneInfo("posix/...")` / `ZoneInfo("right/...")` raise `ZoneInfoNotFoundError` even on hosts whose tzdata ships those legacy trees (the provider's db walk permanently skips them), where CPython's raw file lookup constructs them. Non-canonical keys; use the plain zone name. Spelling asymmetry: `from datetime import ZoneInfo` works in TPy (the class's forced home) but fails under CPython -- the portable spelling is `from zoneinfo import ZoneInfo`. | **Done** (test `stdlib/datetime_zoneinfo_api`) |
| **Permanent** | `ZoneInfo.no_cache` / `clear_cache`: their whole observable effect is identity-distinct same-key instances (inexpressible in a value type -- exactly where CPython's identity-equality diverges from equal-by-key) and mid-run tz-db reload (conflicts with the pin-once provider; the tzset-re-resolve TODO item is the sanctioned reload path). Same tier as user `tzinfo` subclasses. Loud absences. | Permanently unsupported |
| **Deferred** | `ZoneInfo.from_file` + `TZPATH`/`reset_tzpath` (backend work: no public TZif-stream parser in the vendored provider; custom search paths need provider support); `time.fold` (rides the aware-`time` deferral); aware `time` (the class keeps no tzinfo; `time.fromisoformat` rejects an offset suffix loudly); user-defined `tzinfo` subclasses (permanent); Windows tz backend. Filed, not silent. | Deferred |

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
- **Field storage is narrow and private, behind Int32-widening
  `@property` getters** (since the v3 packing pass): `date` stores
  `Int16/Int8/Int8` (4 bytes), `time` `Int8 x3 + Int32` (8),
  `datetime` adds the inline tz block -- offset `Int64` + interned
  name id `Int32` + aware flag -- for 24 bytes total, all trivially
  copyable. Public attribute types are unchanged (`d.year` is Int32 via
  the getter), so user arithmetic never touches the narrow storage and
  the sub-default-int promotion question stays orthogonal. `timezone`
  is a 16-byte `(offset Int64, name id Int32)` mirror of datetime's tz
  block; names live in a process-global append-only intern table
  (`tpy/stdlib/tz_intern.hpp`, mutex-guarded, id 0 = unnamed --
  distinct from an interned empty string), touched only on
  construction-with-name and tzname/repr/%Z -- offset math never
  consults it. The `.tzinfo` getter reconstructs the `timezone` value
  on demand (cold path).
- **Component ranges match CPython's attributes exactly.** Date/time components are naturally bounded
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
- v3 (landed): fixed-offset `timezone` (a frozen value: offset `timedelta` +
  optional name, eq/hash by offset only like CPython). Stored as
  `Optional[timezone]` (`std::optional<timezone>`); `optional<value-type>` is
  still a value type, so `datetime` stays a value type end-to-end.
  **Awareness is a runtime property** of the single `datetime` type: mixing
  naive and aware raises `TypeError` at RUNTIME for ordering/subtraction and
  compares unequal for `==`, exactly like CPython (the earlier design note
  that mixing would be "rejected at compile time" was unimplementable with
  one runtime-tagged type and is superseded -- this is now full parity, not
  a divergence). `timezone.utc` is spelled via the module-level `UTC` alias
  (CPython 3.11+); the class attribute is a loud compile error (a class
  constant of the record's own type is not expressible -- TODO.md).
  `replace()` uses CPython's `tzinfo=True` sentinel signature (a record
  value cannot be a TPy param default; the bool arm means "keep"). Note
  the sentinel is the pure-Python `_pydatetime` reference behavior; the
  C-accelerated CPython module rejects an explicit bool with TypeError,
  so TPy accepting a spelled-out `tzinfo=True` is a declared
  accepted-permissive nuance (the omitted-arg path is identical in all
  three).
- v4 (landed): the value-typed `ZoneInfo` widening -- the tz slot is the
  closed value union `timezone | ZoneInfo | None`
  (`std::variant<std::monostate, ZoneInfo, timezone>` at params/returns;
  the packed datetime keeps scalars: a kind tag naive/fixed/zoneinfo +
  fold packed in one Int8, with `_tz_name_id` doubling as the zone id).
  `ZoneInfo` is one interned zone id (the provider pins the zone handle
  process-globally), equal-by-key -- behaviorally CPython's per-key
  instance cache; the divergence only becomes reachable if `no_cache()`
  ever lands. Offsets are per-instant from wall time + fold; eq/ordering
  use CPython's `mytz is ottz` identity rule translated to same-zone-id
  (wall compare, fold ignored), hash normalizes to fold=0, and
  subtraction short-circuits same-zone to the wall difference.

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
  to minimal facade primitives: `local_utc_offset_seconds(epoch)` /
  `local_zone_abbrev(epoch)` for the local zone, and (v4/v4.1) the
  named-zone set `zone_lookup(key)` / `zone_key(id)` /
  `zone_wall_{offset,dst}_seconds(id, wall, fold)` /
  `zone_wall_abbrev(id, wall, fold)` / `zone_utc_offset_seconds(id, epoch)`
  / `zone_db_count()` / `zone_db_key_at(i)` (available_timezones)
  over a process-global zone intern table (id 0 = not found; provider
  errors never cross the facade -- `ZoneInfoNotFoundError` is raised in
  TPy). DST savings are derived from neighboring standard intervals
  (CPython's heuristic): under `USE_OS_TZDB` the tzfile carries only an
  is_dst flag. The concrete C++ tz provider sits behind it, so it can be
  swapped without touching TPy stdlib or generated code.
- **TZ resolution (v3).** The provider resolves the zone ONCE at first use
  and pins it for process life: `TZ` set -> POSIX rule strings via the
  provider's POSIX reader (`ptz.h`), IANA names via `locate_zone`, a
  leading `:` forces the database lookup, empty or unparseable -> fixed
  UTC (glibc semantics); `TZ` unset -> `current_zone()` (/etc/localtime).
  `time.tzset()` exists as a CPython-parity no-op: the canonical
  set-TZ-then-tzset-then-use pattern works unchanged, but a `TZ` change
  AFTER local time was first used is not re-read (documented divergence;
  libc rereads per call).
- **Local-inverse operations.** Naive `timestamp()` and naive-source
  `astimezone()` share the CPython `_mktime` iterative fixed-point solve
  over `local_utc_offset_seconds` (`_local_mktime_s`) -- a single forward
  lookup would be silently wrong in the 1-2h window around DST
  transitions. The value's `fold` picks the branch (fold=0: gap resolves
  to the later instant, fold to the earlier; fold=1 the other way), and
  the same solve at fold=0 doubles as the fold detector for naive-local
  `fromtimestamp` (reproduces the instant iff it is the first pass).
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

v3 `strftime`/`strptime` implement the full documented directive set in TPy
with hardcoded C-locale (English) month/day tables (`LC_TIME` is never
consulted -- see divergences): `%a %A %b %B %c %d %f %G %H %I %j %m %M %p
%S %u %U %w %W %x %X %y %Y %z %Z %%`. `%c`/`%x`/`%X` are the C/POSIX-locale
compositions. Verified strftime edge behaviors (byte-compared vs CPython on
glibc): `%Y`/`%G` are NOT zero-padded (`'42'`) although isoformat is;
unknown directives pass through verbatim incl. the `%`; a trailing lone `%`
is kept; `%z`/`%Z` render empty for naive values and `%z` carries seconds
(`+053015`) and microseconds when the offset has them. strptime mirrors
`_strptime.py`: longest-first bounded numeric fields (space-padded `%d`),
case-insensitive names and literals, format whitespace matches 1+ input
whitespace, `%f` right-pads 1-6 digits (7+ digits leave unconverted data),
`%y` pivots at 69, `%z` accepts `Z`(case-sensitive)/`+HH:MM[:SS[.f]]`/
`+HHMM[SS]` with colon-consistency errors, week numbers resolve via
`%U`/`%W`+weekday or ISO `%G`/`%V`/`%u` (incompatible mixes raise).
`fromisoformat` ports the 3.11+ grammar (basic `YYYYMMDD`, week dates, any
single separator char, comma fractions, 7+ fraction digits TRUNCATE --
deliberately a separate fraction rule from strptime's `%f`), digit-strict
like the C implementation.

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
- **Naive-vs-aware mixing is full runtime parity, no divergence** (v3):
  ordering and subtraction raise `TypeError`, `==` is `False`, matching
  CPython exactly. (Supersedes the earlier compile-time-rejection note.)
- **Extreme-arg construction** raises the correct catchable exception because
  we validate on BigInt before the Int32 store (no divergence -- noted here
  because the naive store-then-check ordering *would* have panicked).
- **A bare POSIX std/dst `TZ` pair without a rule suffix**
  (`TZ=EST5EDT`, no `,M3.2.0,...`) yields a constant permanent-DST
  offset: the vendored POSIX reader has no seasonal transitions for
  that form, while glibc fills in default US DST rules. Rule-suffixed
  strings and IANA names are unaffected. Spell the rules explicitly.
- **`TZ` is honored but pinned at first use** (v3): IANA names, POSIX rule
  strings, empty/unparseable->UTC all match glibc, and `time.tzset()`
  before first local-time use completes the canonical CPython pattern --
  but a `TZ` mutation AFTER local time was first used is not re-read
  (libc rereads per call). Also, for degenerate `TZ` values (empty or
  unparseable) the synthesized zone ABBREVIATION is `'UTC'` where glibc
  invents host-specific names (`'Universal'`, the bad spec's text);
  offsets always match.
- **Locale-dependent directives are permanently C-locale**: `%a %A %b %B
  %p %c %x %X` use hardcoded English tables; `LC_TIME` is never
  consulted (CPython delegates to the platform strftime). Programs
  running under a non-English `LC_TIME` diverge by design; tests never
  set a locale, so committed output is host-independent.
- **`strptime` `%Z` accepts only `UTC`/`GMT`** (case-insensitive).
  CPython additionally accepts the host's live `time.tzname` pair, which
  is itself host- and TZ-dependent; TPy pins the portable subset
  (stricter, loud `ValueError` for other tokens).
- **`timezone.utc` / `datetime.UTC` spelling**: the module-level `UTC`
  alias (real CPython 3.11+ surface) is the supported spelling;
  `timezone.utc` is a loud compile error (class-level constant of the
  record's own type is not expressible; filed in TODO.md). Relatedly,
  `tz is UTC` identity tests are compile errors on value types (existing
  policy) -- use `==`.
- **Aware `time` is deferred**: the `time` class carries no tzinfo, its
  `replace()` has no tzinfo param, and `time.fromisoformat` raises
  `ValueError` on an offset suffix where CPython returns an aware time
  (loud rejects-valid, not silent dropping).
- **`fold` (landed in v4)**: the attribute, ctor/`replace()` params, and
  PEP 495 gap/fold selection are implemented for `datetime` (naive-local
  and zoneinfo alike). `time.fold` stays out with the aware-`time`
  deferral (CPython's `time.fold` is inert for zone math anyway --
  `ZoneInfo.utcoffset(None)` is None).

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
- **v3:** `strftime` directive matrix (year<1000 `%Y`, ISO rollovers,
  midnight/noon `%I%p`, seconds-bearing `%z`), `strptime` acceptance rules +
  error tokens, `fromisoformat` accept/reject matrix + round-trips,
  fixed-offset `timezone`/aware core (mixing rules, UTC-normalized eq/hash,
  replace forms), and deterministic local-time cases under a pinned `TZ`
  (Europe/Warsaw incl. DST gap/fold timestamps; an EST5EDT POSIX rule
  string) -- both toolchains honor the in-test `TZ` set via
  `os.environ["TZ"]` + `time.tzset()` before first use, making the
  previously "untestable" local-inverse behavior a committed byte-compare.

Reference-type note does not apply (these are value types), but tests must
still avoid host-dependent output (local-time cases print comparisons /
relationships, never absolute local timestamps).

## File layout

- `lib/tpy/datetime.py` -- the module: the five classes, UTC, wall-clock
  and backend code. Not a package: a class-heavy package __init__ calling
  submodule helpers from method bodies trips the circular-include
  limitation filed in BUGS.md, so the engines live in flat private
  siblings (the CPython `_strptime` shape).
- `lib/tpy/_datetime_cal.py` -- calendar math + C-locale name tables
  (leaf; shared by both engines and the classes).
- `lib/tpy/_datetime_fmt.py` -- strftime engine, isoformat/offset/label
  helpers (pure string builders over component values).
- `lib/tpy/_datetime_parse.py` -- strptime scanner + fromisoformat
  parsers; tuple-returning (construction happens in datetime.py, keeping
  the parse layer free of the class layer).
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
