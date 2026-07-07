# Python Stdlib Coverage

Tracks TPy's coverage of the CPython standard library. This is a living
document -- update it whenever a stdlib module or item gains/loses support.

**Scope**: modules present in CPython's `stdlib` plus the implicit `builtins`
module (len, print, list, dict, exceptions, etc.). TPy-native extras
(`tplib.Box`, `tplib.ArrayList`, `tplib.FixStr`, `tplib.json`) are tracked in
`LANGUAGE_FEATURES.md` since they don't have CPython equivalents.

Status legend:
- **Done** -- matches CPython semantics for the items listed below
- **Partial** -- usable; some items missing or differ from CPython
- **Stub** -- minimal scaffolding, most items missing
- **Missing** -- not started
- **Blocked** -- needs a language/runtime feature before meaningful work

Priority reflects impact for typical Python programs (P0 = essential for
most real programs; P3 = rarely needed / specialized).

Approach legend: **native** (thin `@native` bindings to libc / OS / existing
C++ libraries), **pure** (pure TPy), **macro** (compile-time macro module),
**mixed** (combination).

---

## Implementation Policy

Stdlib modules should be written in **pure TPy (.py)** by default. C++ code
is introduced only where it genuinely must be:

1. **OS / libc / syscalls** -- wall-clock time, filesystem, sockets, process
   spawning, memory maps. There is no TPy-level equivalent.
2. **Existing C++ library bindings** -- regex engines (std::regex, PCRE2),
   crypto (OpenSSL), compression (zlib), etc. We wrap, we don't rewrite.
3. **Performance-critical inner loops where TPy generation is demonstrably
   worse.** Must be justified with a benchmark; not a default assumption.

Everything else is pure TPy. If the language is missing something that blocks
a clean pure-TPy implementation -- closures, generators, a specific dunder,
module-level mutable state, `*args`/`**kwargs`, runtime type info -- the
**right fix is to extend the language**, not to drop into C++. A stdlib that
hits language gaps is the best possible driver for language work, because
each gap has a concrete user-visible payoff.

For modules that do need native code, follow the **thin-binding pattern**:
import raw symbols via `@native` into a minimal module (e.g.
`tplib.cppstd.re`, `tpy._os.libc`), then build the CPython-compatible surface
as pure-TPy wrappers on top. The native layer should expose the underlying
primitive faithfully -- no Python semantics baked in -- and the pure-TPy
layer translates to Python semantics (error handling, optional args,
defaults, naming, iteration protocols). Benefits:

- One place to swap backends (e.g. std::regex -> PCRE2) without touching
  user-visible code.
- CPython stubs in `lib/cpy/` can mirror the pure-TPy wrappers directly --
  only the bottom layer differs.
- Type checker, IDE, and LLMs see real Python code, not opaque C++ symbols.
- Macro-based modules can inspect the pure-TPy layer.

### C++ helper convention

When a stdlib module genuinely needs a small C++ helper beyond raw libc /
`std::` calls -- e.g. wrappers that return `std::tuple` to bridge CPython's
multi-return APIs (`math.modf`, `math.frexp`) or that compose a handful of
`std::` calls into a Python-semantics primitive (`math.ulp`) -- place it in
`runtime/cpp/include/tpy/stdlib/<module>.hpp` under namespace
`tpy::stdlib::<module>`. Example: `tpy/stdlib/math.hpp` defines
`tpy::stdlib::math::modf(...)`.

This keeps stdlib-backing helpers cleanly separated from TPy's own runtime
core (`tpy::` at the top level, covering `tpy::BigInt`, `tpy::ordered_map`,
`tpy::varargs`, etc.). Path mirrors namespace, standard C++ convention, and
future modules slot in predictably: `tpy/stdlib/random.hpp` ->
`tpy::stdlib::random`, `tpy/stdlib/time.hpp` -> `tpy::stdlib::time`, and so
on.

Complementary Python-side convention (already in use): stdlib `.py` modules
declare `# tpy: cpp_namespace("tpystd::<module>")` so the generated code for
the pure-TPy wrappers lives under `tpystd::math::`, `tpystd::bisect::`, etc.
Three layers, three namespaces, all distinct:

| Layer | Namespace | Example |
|---|---|---|
| Raw C/C++ primitives | `std::` (or third-party) | `std::log`, `std::modf` |
| TPy-authored C++ helpers | `tpy::stdlib::<module>::` | `tpy::stdlib::math::modf` |
| Generated code for pure-TPy wrappers | `tpystd::<module>::` | `tpystd::math::gcd` |

The `approach` column in the overview table below reflects the **public-facing**
strategy. Internally nearly every module ends up "mixed" if it touches the OS --
the distinction is whether the Python-visible logic lives in .py or C++.

### Test location convention

Stdlib-module tests live under `tests/cases/stdlib/<module>/` -- one directory
per CPython module, matching the import name (`stdlib/bisect`, `stdlib/heapq`,
`stdlib/math/...` for grouped subcases, etc.). This keeps the stdlib surface
discoverable as a group (`pytest -k stdlib/`) and separates it from feature
tests under `cases/<feature>/`. Earlier stdlib tests that ended up under
`cases/imports/` or `cases/builtins/` are being migrated as they're touched.

Examples of the policy in action:

- `bisect` -- fully pure TPy over the `Comparable` protocol. No native code.
- `math` -- thin `@native` bindings to `std::log`, `std::sqrt`, etc. (libc
  math is the primitive); no TPy-visible C++ logic beyond the bindings.
  `log(x, base)`, `radians`, `degrees` are pure-TPy overloads/wrappers.
- `random` -- currently thin binds to `std::rand`; long-term should be a
  pure-TPy Mersenne Twister (matches CPython), with only `os.urandom`-style
  entropy as the native primitive.
- `re` -- thin `tplib.cppstd.re` / `_bindings.pcre2.re` binding (the regex engine
  is the primitive), with a pure-TPy facade for the Python surface and a
  pure-TPy syntax translator.
- `pathlib` -- pure TPy over thin filesystem-syscall bindings.
- `json` (stdlib-compat) -- pure TPy on top of `tplib.json`'s parser/writer.

---

## Module Overview

| Module | Priority | Status | % | Approach | Blockers / Notes |
|---|---|---|---|---|---|
| [`builtins`](#builtins) | P0 | Partial | ~75% | mixed | Implicit import. Core types + most common functions + most exception types present and catchable (`Index/Key/Lookup/Value/Type/Attribute/Assertion/OS/FileNotFound/Permission/Connection (+BrokenPipe/Reset/Refused/Aborted)/ZeroDivision/Overflow/FloatingPoint/Arithmetic/Runtime/Recursion/EOF/NotImplemented/Memory/StopIteration`); fixed-int arithmetic overflow stays panic by design (future policy switch). Missing: `frozenset`, `complex`, `memoryview`, `input`, `format`, `ascii`, `callable`, `id`, `type(x)` runtime. D16 dyn-attrs (`getattr`/`setattr`/`delattr`/`hasattr` for both literal and runtime names) fully shipped. See [builtins](#builtins) for per-item status |
| [`math`](#math) | P0 | Done | ~99% | mixed | Thin libc bindings + pure TPy wrappers. All CPython funcs present with matching signatures (`Iterable[float]` for fsum/sumprod/dist; `prod` has Int32 / int (BigInt) / float overloads). Remaining gap: tuple as iterable (blocked on tuple-iteration bundle) |
| [`time`](#time) | P0 | Partial | ~50% | mixed | Thin clock/sleep syscalls. `time`, `sleep`, `perf_counter`, `monotonic`, `time_ns`, `perf_counter_ns`, `monotonic_ns`, `process_time`, `tzset` (CPython-parity no-op: TPy's tz provider pins TZ at first use) all done. Missing `struct_time`/`strftime`/`gmtime`/`localtime`/timezone constants |
| [`sys`](#sys) | P0 | Stub | ~20% | mixed | Thin syscall bindings + pure TPy. `argv`, `stdout`, `stderr`, `exit`, `maxsize` done; needs `stdin`/`path`/`version_info` |
| [`os`](#os) | P0 | Partial | ~72% | mixed | Filesystem queries (`getcwd`/`chdir`/`listdir`/`scandir`/`getenv`) + `stat`/`lstat`/`fstat` -> `stat_result`, `scandir` -> `DirEntry`; mutating ops (`mkdir`/`makedirs`/`rmdir`/`removedirs`/`remove`/`unlink`/`rename`/`replace`/`symlink`/`readlink`/`link`/`truncate`/`ftruncate`/`chmod`/`chown`/`utime`/`fsync`) over raw POSIX with the shared PEP 3151 errno->OSError table (structured `.errno`/`.strerror`/`.filename` + CPython-exact `str(e)`); low-level fd I/O (`open`/`close`/`read`/`write`/`lseek`/`pipe`/`dup`/`dup2` + `O_*`/`SEEK_*`); `access`(+`*_OK`); `urandom`; process/system queries (`getpid`/`getppid`/`getuid` family/`getlogin`/`umask`/`cpu_count`/`strerror`/`isatty`/`get_terminal_size`); `fspath`; module constants (`name`/`sep`/...); `environ` snapshot mapping + `putenv`/`unsetenv` + `pop`/`setdefault`/`update`/`clear`/`copy`; `walk` (topdown + bottomup, `followlinks`, `onerror`-callback + default error-skip). Process spawning deferred |
| [`os.path`](#ospath) | P0 | Partial | ~96% | mixed | Pure-string POSIX surface (`join`/`split`/`splitext`/`basename`/`dirname`/`isabs`/`normpath`/`splitdrive`/`commonprefix`/`commonpath`/`normcase` + constants) plus filesystem queries (`exists`/`lexists`/`isfile`/`isdir`/`islink`/`getsize`/`abspath`/`realpath`(+`strict=`)/`relpath`/`getmtime`/`getatime`/`getctime`/`samefile`/`samestat`/`ismount`/`expandvars`/`expanduser`). CPython byte-compatible against `posixpath` |
| [`pathlib`](#pathlib) | P0 | Missing | 0% | -- | Class-heavy; depends on filesystem bindings |
| [`io`](#io) | P0 | Partial | ~40% | pure | `StringIO` / `BytesIO` (chunked storage, write/read/`read(size)`/readline/seek/tell/truncate/iter/context-manager). `FileIO` (raw fd adopt) + `BufferedReader` (buffered binary read over any `@dynamic RawBinaryIO` source via `Box[dyn]` -- an fd-backed `FileIO` or a userspace reader like `ssl.SSLRawIO`; the layer `socket.makefile()`/`http.client` read through). `Readable` / `Writable` / `BinaryReadable` / `BinaryWritable` protocols on tpy core, re-exported from `io`. `SEEK_SET`/`CUR`/`END` + `DEFAULT_BUFFER_SIZE` exposed. Missing: `IOBase` ABC hierarchy (deliberately deferred -- protocols cover the static-dispatch use case), `TextIOWrapper`, `BufferedReader.peek`/`readinto`, encoding/newline/errors kwargs |
| [`json`](#json) | P0 | Partial | ~72% | pure | `loads` / `dumps` / `load(fp)` / `dump(obj, fp)` + `JSONDecodeError` done over a recursive union `JsonValue`. CPython byte-compatible across cpy phase. Missing: `JSONEncoder` / `JSONDecoder`, most `dumps`/`loads` kwargs |
| [`re`](#re) | P0 | Partial | ~50% | pure | Pure-TPy facade over `_bindings.pcre2` raw bindings. PCRE2 vendored under `runtime/cpp/third_party/pcre2/` (5MB) and built bundled by default; `--pcre2={bundled,system,auto}` selects backend. compile/search/match/fullmatch/findall/sub (with `count`)/split + Pattern/Match classes + IGNORECASE/MULTILINE/DOTALL/VERBOSE/ASCII flags + `re.error`. Missing: named-group accessors, bytes input, compile cache |
| [`collections`](#collections) | P0 | Partial | ~15% | pure | `Counter` v1 (construct/[]/len/in/total/most_common(n)/update/subtract) done; `elements()` + `+ - & |` deferred on filed compiler blockers. OrderedDict trivial (have ordered_map); deque needs C++ struct; defaultdict/namedtuple need macros |
| [`itertools`](#itertools) | P0 | Partial | ~35% | pure | Done: count, repeat, cycle, islice(it, stop), takewhile, dropwhile, filterfalse. `chain`/`product` family blocked on variadic tuples; `starmap`/`accumulate`/`pairwise`/`compress` on distinct compiler gaps (filed in BUGS.md/TODO.md); `tee`/`groupby`/`batched` on buffering / runtime-sized tuples |
| [`functools`](#functools) | P0 | Partial | ~25% | pure | `reduce(func, a, initial)`, `reduce(func, a)`, `total_ordering` done. `cmp_to_key`, `wraps` blocked on specific compiler / macro-infrastructure gaps (see section). partial/lru_cache/singledispatch/cached_property/partialmethod need closures + macros |
| [`random`](#random) | P1 | Partial | ~90% | pure | Pure-TPy MT19937 + CPython's distribution suite, byte-identical to CPython on the same seed. Done: `Random` class, `random`, `seed(Int32)` (negatives mapped to abs), `seed()` no-arg auto-seed via OS entropy, `getrandbits(k)` for arbitrary k, `randint`, `randrange`, `randbytes`, `choice`, `shuffle`, `uniform`, `triangular`, `gauss`, `normalvariate`, `lognormvariate`, `expovariate`, `paretovariate`, `weibullvariate`, `gammavariate`, `betavariate`, `vonmisesvariate`. Missing: `choices`/`sample`/`SystemRandom`/`binomialvariate`/`getstate` (Tier 3). See module docstring TODOs |
| [`struct`](#struct) | P1 | Partial | ~60% | macro | unpack/calcsize only; `pack` needs statement-expr or buffer builder |
| [`bisect`](#bisect) | P1 | Done | 100% | pure | All four functions implemented generically over `Comparable` |
| [`enum`](#enum) | P1 | Partial | ~50% | macro | Enum/IntEnum/auto; missing functional API, lookup by name/value, iteration |
| [`dataclasses`](#dataclasses) | P1 | Partial | ~80% | macro | frozen/order/inheritance/asdict/astuple/__post_init__; missing InitVar, replace(), metadata |
| [`typing`](#typing) | P1 | Partial | ~60% | native | Protocols/Sized/Iterator/TypedDict/Unpack; missing Generic, TypeVar, ParamSpec, ClassVar |
| [`datetime`](#datetime) | P1 | Partial | ~95% | pure | v1-v4 landed: `timedelta` + `date` + `time`/`datetime` incl. fixed-offset `timezone` AND `ZoneInfo` awareness (closed value union tz slot), PEP 495 fold, strftime/strptime/fromisoformat, timestamp/astimezone (iterative local-inverse), TZ-honoring Hinnant date backend, dt.date()/dt.time() accessors, `timedelta` float operators (`td / number`, `td */` float, round-half-to-even). Deferred: aware `time`, `timedelta` float constructor args (`timedelta(hours=1.5)` -- rejected; use integer components) |
| [`zoneinfo`](#datetime) | P1 | Partial | ~90% | pure | v4: `ZoneInfo` (value type, interned zone id, per-instant DST offsets; equal-by-key = CPython's per-key cache behavior) + `ZoneInfoNotFoundError` + `available_timezones()` (provider's zone db -- every key constructs; CPython's placeholder entries Factory/localtime excluded, declared) + `ZoneInfo.fromutc`. Permanent: `no_cache`/`clear_cache` (identity semantics + mid-run db reload are outside the value-typed pin-once model). Deferred: `from_file`, `TZPATH`/`reset_tzpath` (backend work) |
| [`csv`](#csv) | P1 | Partial | ~70% | pure | `reader` / `writer` (list[str] rows) + `DictReader` / `DictWriter` (dict[str, str] rows) over the io `Readable`/`Writable` protocols. Excel default dialect + delimiter/quotechar/doublequote/skipinitialspace/lineterminator kwargs; writer is QUOTE_MINIMAL. CPython byte-compatible. DictWriter matches CPython's write-side defaults (restval="" for a missing field, ValueError for a key not in fieldnames). Missing: escapechar, quoting constants, Dialect objects/register_dialect, Sniffer, DictReader restval/restkey (short rows pad "", long rows drop extras -- dict[str,str] can't hold None or a list), DictWriter extrasaction='ignore' |
| [`base64`](#base64) | P1 | Partial | ~95% | pure | Pure-TPy b64/b32/b16 encode+decode + urlsafe/standard variants + altchars=/validate=/casefold=/map01= kwargs + encodebytes/decodebytes. bytes/bytearray/str accepted on decoders (matches CPython). Missing: b85/a85 (rare, separate algorithms); `memoryview` depends on builtin gap |
| [`hashlib`](#hashlib) | P1 | Partial | ~20% | pure | SHA-256 pure-TPy. MD5/SHA-1/SHA-512 are straight follow-ups (same class pattern, different round functions / endian). BLAKE2/SHA-3 later. Optional OpenSSL backend also later |
| [`argparse`](#argparse) | P1 | Partial | ~88% | macro | Builder-trace macro (Phase 7); positionals/optional flags, all 7 actions, all 4 nargs, type=int\|float\|str + fixed-width ints + Float32 + custom records via `from_arg` (all 4 nargs + append/extend), choices/required/dest/help/metavar, Optional[T]/Optional[list[T]] for absent flags, list-literal defaults, bare parse_args() reads sys.argv[1:], --help/-h auto-generation, add_help=False opt-out, prog=/usage=/epilog= help customization, subparsers (flat-namespace; per-sub fields land as Optional[T] on the top namespace). Missing: mutually-exclusive groups, argument groups, runtime-derived `prog`, terminal-width help wrap, per-sub `--help` auto-emit, BooleanOptionalAction, parents=, allow_abbrev, fromfile_prefix_chars, custom formatter classes, action=<callable> |
| [`logging`](#logging) | P2 | Missing | 0% | -- | Module-level state + handler architecture |
| [`configparser`](#configparser) | P2 | Missing | 0% | -- | Depends on `io` |
| [`urllib.parse`](#urllibparse) | P2 | Partial | ~75% | pure | Pure TPy; CPython byte-parity. urlsplit/urlparse/urlunsplit/urlunparse/urljoin, quote family, urlencode, parse_qsl. Results are records not namedtuples (no indexing/unpack). parse_qs, bytes variants, urldefrag deferred |
| [`heapq`](#heapq) | P2 | Partial | ~92% | pure | Pure TPy over `list[T: Comparable]`. All non-variadic ops + `merge(*iterables: list[T])` (lazy, stable n-way merge). `merge` gaps vs CPython: inputs must be `list[T]` not arbitrary iterables (needs dynamic-iterator erasure + protocol varargs), and `key=`/`reverse=` absent (`key=` blocked on the readonly-through-generic-T callable gap in BUGS.md; `reverse=` a cheap follow-up) |
| [`copy`](#copy) | P2 | Missing | 0% | -- | `copy()` deep semantics need intrinsic support |
| [`textwrap`](#textwrap) | P2 | Missing | 0% | -- | Pure TPy candidate |
| [`decimal`](#decimal) | P2 | Missing | 0% | -- | Large surface; candidate for BigInt-based pure impl or native lib |
| [`fractions`](#fractions) | P3 | Missing | 0% | -- | Pure TPy over BigInt |
| [`statistics`](#statistics) | P2 | Missing | 0% | -- | Pure TPy candidate |
| [`pickle`](#pickle) | P2 | Blocked | 0% | -- | Needs dynamic type info + `io` |
| [`shelve`](#shelve) | P3 | Blocked | 0% | -- | Needs pickle |
| [`inspect`](#inspect) | P2 | Blocked | 0% | -- | Needs runtime type/func introspection |
| [`asyncio`](#asyncio) | P1 | Partial | ~40% | pure | v1: `run`/`sleep`/`create_task`/`Task[T]`/`Future[T]`/`Event`/`CancelledError` + thread-local executor with slot table, runnable deque, timer min-heap, cancel-drain at run-end. Bound coroutines: `c = f()` binds a move-only single-use handle consumed later by `await c`/`create_task(c)`/`run(c)`; `run`/`create_task` enforce CPython's coroutine-only TypeError contract at compile time. v1.5 M5+M6: `async with` (cleanup-only), `async for` + `StopAsyncIteration`. v1.5 M8: `wait_for`/`TimeoutError`. v1.5 M9: `gather(*tasks)` (homogeneous variadic-positional) + `gather_list(tasks)` (homogeneous list shape). v2 sync primitives: `Lock`, `Semaphore`, `BoundedSemaphore`, `Queue` (FIFO `list[Waker]` waiter queue, `async with`-capable; `BoundedSemaphore` is a `Semaphore` subclass rejecting over-release; `Queue[T]` adds getter/putter/joiner waiter sets + `maxsize`/`put`/`get`/`*_nowait`/`join`/`task_done`). v2 I/O reactor M1: `Reactor` (epoll on Linux, kqueue on macOS / *BSD behind the shared `tpy_epoll_*` C ABI) + `get_running_loop().sock_recv`/`sock_sendall` on non-blocking sockets (`socket.setblocking`), executor blocks in `epoll_wait` / `kevent` bounded by the timer heap. v2 M2: `get_running_loop().sock_accept`/`sock_connect` (driven via the public socket methods, parking on `BlockingIOError`). v2 streams: `open_connection` -> `StreamReader` (`read`/`readexactly`/`readline`/`readuntil`/`at_eof`) + `StreamWriter` (`write`/`drain`/`close`/`wait_closed`/`is_closing`), socket shared via `Rc[socket]`; `IncompleteReadError`. v2 streams server: `start_server(handler, host, port)` -> `Server` (background accept loop spawning the async `handler` per connection with a `(StreamReader, StreamWriter)` pair; `serve_forever`/`close`/async-with, `server.sockets[0].getsockname()` for the bound address), built on the async-fn->Callable coercion. v2 SIGINT graceful shutdown: `asyncio.run` installs a SIGINT handler -> cancels the root task, runs its cleanup, raises `KeyboardInterrupt` (SIGINT-only, matching CPython; SIGTERM left at default). Missing: CPython-shape *heterogeneous* variadic `gather[*Ts](*coros) -> tuple[*Ts]` (needs variadic generics + async-def `*args` codegen); `StreamReader.readuntil` `limit`/`LimitOverrunError` (the method shipped); graceful SIGTERM (divergent enhancement); multi-thread (v3+) |
| `signal` | P2 | Stub | ~5% | pure | Minimal: `raise_signal` + `SIGINT`/`SIGTERM` constants over a libc binding -- backs asyncio.run's SIGINT graceful shutdown and lets programs self-signal portably (CPython has the same surface). No `signal.signal` / handler-registration API (asyncio installs its SIGINT handler internally via the `posix_signal` binding) |
| `errno` | P2 | Stub | ~5% | pure + C | Minimal: the constants TPy's own stdlib maps to exception subclasses -- network domain (`EAGAIN`/`EWOULDBLOCK`/`EINPROGRESS`/`EPIPE`/`ECONNRESET`/`ECONNREFUSED`/`ECONNABORTED`, from socket_impl.cpp) + file domain (`ENOENT`/`EEXIST`/`EACCES`/`EPERM`/`EISDIR`/`ENOTDIR`/`EBADF`/`ETIMEDOUT`, from os_impl.cpp) -- read from the platform `<errno.h>` via `tpy_const_*` native globals so values are host-correct. Enables `e.errno == errno.ENOENT` against `OSError`'s structured attributes (now populated by os/file and socket raises alike). Grows as constants gain consumers |
| [`threading`](#threading) | P1 | Not ported | 0% | -- | OS threads exist as `tpy.thread` (Send+move); CPython `threading` port not prioritized |
| [`multiprocessing`](#multiprocessing) | P2 | Blocked | 0% | -- | Needs process spawning + IPC |
| [`subprocess`](#subprocess) | P1 | Blocked | 0% | -- | Needs process spawning |
| [`socket`](#socket) | P1 | Partial | ~75% | pure + C | IPv4 TCP client/server (blocking + non-blocking via `setblocking`), `socketpair`, `create_connection`/`create_server`, `send`/`recv`/`sendall`/`shutdown`/`setsockopt_int`/`getsockopt_int`/`getsockname`/`getpeername`, `gethostbyname`, context-manager, `SocketError`/`gaierror`/`BlockingIOError` (subclass `OSError`); every raise carries the structured `.errno`/`.strerror` attributes (`gaierror` puts the EAI_* code in `.errno`); errno maps to the PEP 3151 `ConnectionError` subclasses (`BrokenPipeError` on EPIPE -- a write to a hung-up peer raises this rather than killing the process, the runtime ignores `SIGPIPE` at startup; `ConnectionResetError`/`ConnectionRefusedError`/`ConnectionAbortedError` on ECONNRESET/ECONNREFUSED/ECONNABORTED), matching CPython. `settimeout`/`gettimeout`/`getblocking` timeout mode (recv/send via SO_RCVTIMEO/SO_SNDTIMEO, connect via a poll wait; timeout -> `TimeoutError`, now an `OSError` subclass). `makefile("rb")` -> `io.BufferedReader` (timeout-aware). `create_connection(addr, timeout=)`. Backed by `_bindings.posix_socket` + `socket_impl.cpp`. Missing: IPv6/`AF_INET6`, `AF_UNIX`, `sendto`/`recvfrom`/`recv_into`, full `getaddrinfo`, `makefile` text/write modes, `setdefaulttimeout`, accept-under-timeout, TLS/`ssl`, Windows |
| [`http`](#httpclient) | P2 | Partial | ~60% | pure | `HTTPStatus` (full IntEnum code set; `.value`/`.name`/value-lookup/int-compare; no `.phrase`/`.description`/`.is_*` -- enum can't carry per-member data). Missing: `HTTPMethod` enum |
| [`http.client`](#httpclient) | P2 | Partial | ~60% | pure | HTTP/1.1 over plaintext (`HTTPConnection`) and TLS (`HTTPSConnection`, via `ssl` -- secure-default context, `context=` override, default port 443): `request`/`getresponse`/`connect`/`close`, `HTTPResponse` (`status`/`reason`/`version`/`read`/`getheader`/`getheaders`), `HTTPException`/`BadStatusLine`/`UnknownProtocol`. Both connection classes nominally inherit a `@dynamic _Connection` protocol so a caller can hold either behind one `Box[_Connection]` and dispatch virtually (TPy method dispatch is static, so a plain subclass would not dispatch through a base reference; nominal inheritance stores the conformer directly as the base, no Adapter). Auto Host/Accept-Encoding/Content-Length (CPython byte-order); body framing via Content-Length, chunked, and connection-close. Reads through `makefile()` -> `io.BufferedReader`. `HTTPConnection(host, port, timeout=)` threads a socket timeout through `create_connection`. Connections are persistent (HTTP/1.1 keep-alive): the socket survives request/getresponse cycles, `HTTPResponse.will_close` mirrors CPython's `_check_close`, and `request()` after `close()` reconnects; the caller drains each response and closes on `will_close` (`getresponse()` does not auto-close on will_close as CPython does -- a declared divergence, since TPy's `SSLSocket.close()` sends close_notify immediately). Missing: low-level putrequest/putheader, str/file/iterable bodies, `email.message`-style `.headers`, proxy/`set_tunnel`, the `CannotSendRequest`/`ResponseNotReady` misuse guards, pipelining (see TODO.md). Note: importing `http.client` now links the TLS backend (mbedTLS) for all users -- TODO.md tracks the use-driven-linking follow-up to scope that to HTTPSConnection users |
| [`urllib.request`](#urllibrequest) | P2 | Partial | ~20% | pure | Simplified `urlopen(url, data=None, timeout=None, context=None)` over `http.client` (GET/POST), returns `HTTPResponse`; `http`/`https` schemes (https routes to `HTTPSConnection` on 443, `context=` is the TLS context like CPython), other schemes -> `URLError` (an `OSError` subclass, like CPython). `timeout` (seconds) honored for connect/recv/send. No opener/handler stack, redirects, proxies, auth handlers, `_GLOBAL_DEFAULT_TIMEOUT` sentinel; `create_default_context()` trusts the vendored Mozilla root bundle, so real https verifies out of the box. See TODO.md |
| [`tplib.requests`](#tplibrequests) | P2 | Partial | ~55% | pure | `requests`-style client on `http.client`. `get`/`post`/`put`/`patch`/`delete`/`head`/`request` with `params`/`headers`/`data`/`json`/`auth`/`timeout`/`allow_redirects`/`cookies`; `Response` (`.status_code`/`.ok`/`.text`/`.content`/`.json()`/`.headers` (`CaseInsensitiveDict`)/`.cookies` (`CookieJar`)/`.url`/`.history`/`.raise_for_status()`); `Session` (default headers/params, Basic `auth`, `max_redirects`, persistent `.cookies`); `RequestException`->`HTTPError`/`ConnectionError`/`Timeout`/`TooManyRedirects`, rooted at `OSError` like CPython requests, so `except OSError` catches them (`timeout` float raises `Timeout` on a slow connect/read; a socket-level connection failure -- refused/reset/broken-pipe/no-host -- is re-wrapped as `ConnectionError`). `allow_redirects=` follows 301/302/303/307/308 via `Location` (method/body rewrite + cross-host auth strip per requests), including http->https redirects. HTTPS: an `https://` URL (or redirect target) routes to `HTTPSConnection` on port 443; `verify: bool|str=True` maps to the TLS context (`True` verified default, `"<path>"` custom CA, `False` disables); `requests.SSLError(ConnectionError)` wraps `ssl.SSLError`. `Session` pools connections per `(scheme, host, port, verify)` and reuses them across requests (HTTP/1.1 keep-alive; a `will_close` response closes the socket, the pooled entry lazily reconnects). Divergences: typed kwargs, untyped `.json()`, no `(connect, read)` timeout tuple; cookies are name-keyed with expiry honored (Max-Age/Expires; RFC 1123 + RFC 850 Expires forms parsed, asctime not); a pooled connection keeps its creation timeout. `verify=True` trusts the vendored Mozilla root bundle, so public https verifies out of the box. A default `User-Agent` (`tpy-requests/<major.minor>`, from the compiler version) is sent unless the caller supplies one, matching requests. See TODO.md |

---

## Cross-cutting Language / Runtime Gaps

These unlock multiple stdlib modules. Listed with the modules each would
unblock.

| Gap | Unblocks | Rough effort |
|---|---|---|
| `io.IOBase` protocol + text/binary wrappers (StringIO, BytesIO, TextIOWrapper) | io, json (stdlib), csv, configparser, pickle, shelve | M |
| Regex engine (std::regex phase 1, PCRE2 phase 2, SRE if needed) | re, argparse quality, urllib | M phase 1, L phase 2 |
| Compile-time conditional compilation / build profiles (F8) | re backend selection, allocator choice, embedded variants, debug/release | M |
| Filesystem bindings (wrap C++ `<filesystem>`) | os.path, pathlib, os, shutil | M |
| async/await + event loop | asyncio (v1 shipped), aiohttp, async generators | XL (v1 done) |
| Threading primitives (Thread, Lock, Event, Queue) | threading, multiprocessing.dummy, concurrent.futures | XL |
| Process spawning (fork/exec or std::process) | subprocess, multiprocessing | M-L |
| Socket primitives | socket, http.client (done), urllib.request (done), smtplib, ftplib | L |
| Runtime type info / reflection for `get_type_hints`, `type(x)`, `isinstance` on concrete | typing runtime, inspect, pickle | L |
| Closure capture for `partial`/`lru_cache` | functools | S-M (may already work via Callable) |

### Existing BUGS.md entries that gate pure-TPy stdlib work

Under the implementation policy (pure TPy over thin native bindings), several
known compiler bugs block clean stdlib modules. These were lower-priority as
isolated issues but become **stdlib prerequisites** when stdlib development
ramps up.

| Tracker entry | Effect on stdlib | Blocks |
|---|---|---|
| _open verification, no entry yet_: Module-level mutable state across compilation units. Verified working for a reference-type module-level singleton (e.g. `random`'s shared RNG instance) and for `global`-reassignment of a value-typed module variable; facade-routed patterns still unverified -- file as a bug if a real failure is reproduced. | Single source of truth for per-process state | `logging` (handlers registry), `sys.path`, `warnings` |

The original "stdlib enablement" workstream (variable re-exports through
native_module facades, init chain propagation, `import pkg.sub` + attribute
access for variables, `from pkg import submod` namespace binding, qualified
type names in annotations, always-qualify `@native` refs) shipped on branch
`stdlib-enablement-imports` plus the earlier `D24 Phase 10` commit -- now
behaviour-tested under `tests/cases/imports/native_facade_*`,
`from_pkg_import_submod`, and `import_pkg_sub_attr_var`.

---

## Module Detail

Item status legend (per-row): **Done** / **Partial** / **Missing** / **Blocked**.

### builtins

Implicitly imported. Surface lives in `lib/tpy/tpy/_builtins/` (types,
functions, exceptions, I/O) and is re-exported by `lib/tpy/builtins.py`.

**Types**

| Item | Status | Notes |
|---|---|---|
| `int` | Done | Arbitrary-precision `BigInt` |
| `float` | Done | IEEE 754 double |
| `bool` | Done | |
| `str` | Done | Context-dependent `std::string` / `std::string_view` |
| `bytes`, `bytearray` | Done | |
| `list` | Done | `std::vector<T>` |
| `dict` | Done | Insertion-ordered `tpy::ordered_map<K, V>`; items()/values()/setdefault alias (CPython semantics); two-arg get(k, default) copies reference values with a warning (BUGS.md tracks the borrow form) |
| `set` | Done | Insertion-ordered `tpy::ordered_set<T>` |
| `tuple` | Done | `std::tuple<...>` |
| `range` | Done | `Range[T]` |
| `slice`, `basic_slice` | Done | Three-arg and two-arg slices |
| `frozenset` | Missing | Immutable set; would be `tpy::ordered_set<T>` with mutation-free surface |
| `complex` | Missing | Not yet planned; niche |
| `memoryview` | Missing | `BytesView` exists for bytes-like; general memoryview over any buffer is bigger scope |
| `type` | Partial | `isinstance(x, T)` works; `type(x)` as a runtime value is not yet supported |
| `object` | Done | Implicit root |
| `None` | Done | |

**Functions -- numeric and conversion**

| Item | Status | Notes |
|---|---|---|
| `abs`, `min`, `max`, `sum` | Done | |
| `pow`, `divmod`, `round` | Done | |
| `bin`, `hex`, `oct` | Done | |
| `chr`, `ord` | Done | |
| `len`, `hash` | Done | |

**Functions -- iteration**

| Item | Status | Notes |
|---|---|---|
| `iter`, `next` | Done | |
| `all`, `any`, `sorted` | Done | |
| `enumerate`, `filter`, `map`, `reversed`, `zip` | Done | |

**Functions -- introspection / attribute access**

| Item | Status | Notes |
|---|---|---|
| `isinstance` | Done | Some CPython cases missing -- see TODO.md entries on protocol/union isinstance in ternary expressions and `@runtime_checkable` |
| `repr` | Done | User-type fallback partial. `repr`/`str`/`print`/f-string of unions all work via the variant visitor |
| `issubclass` | Missing | |
| `callable` | Missing | Compile-time evaluable under static dispatch |
| `getattr`, `setattr`, `delattr` (literal-name dynamic-fallback) | Done (D16 v1) | 2-arg literal-name form routes to `__getattr__` / `__setattr__` / `__delattr__`. Declared-member names rejected (use direct attribute access). See `docs/DYNAMIC_ATTRS_DESIGN.md` |
| `hasattr`, 3-arg `getattr` | Done (D16 v1.5) | Phases 7+8 of D16; lambda IIFE wraps the dunder call in try/catch on `AttributeError`. Zero-cost on the happy path under modern table-based EH |
| Dynamic-name 2-arg builtins | Done (D16 v1.5 phase 9) | Option A: routes unconditionally to the dunder when the name is a runtime expression. CPython divergence documented in `docs/DYNAMIC_ATTRS_DESIGN.md` divergence #8 |
| `id` | Missing | `tpy.unsafe.unsafe_address_of` exists as an approximation; `id` semantics differ under static compilation |
| `type(x)` (runtime value) | Missing | See TODO.md "type(); T = type(x); z = T()" |
| `vars`, `dir` | Missing | Not meaningful without runtime object introspection |
| `ascii`, `format` | Missing | f-strings cover most `format` uses |

**Functions -- I/O**

| Item | Status | Notes |
|---|---|---|
| `print` | Done | |
| `open`, `open_text`, `open_binary` | Done | `TextIO` / `BinaryIO` context managers |
| `input` | Missing | Needs stdin reader |

**Descriptors / class utilities**

| Item | Status | Notes |
|---|---|---|
| `@property` | Done | See LANGUAGE_FEATURES Properties |
| `@staticmethod` | Done | |
| `@classmethod` | Open | Tracked in FEATURE_ROADMAP Future Extensions (sugar over `type[T]`) |
| `super()` | Done | See LANGUAGE_FEATURES |

**Dynamic / not-meaningful under AOT**

| Item | Status | Notes |
|---|---|---|
| `eval`, `exec`, `compile` | Missing | Not meaningful without a runtime interpreter |
| `globals`, `locals` | Missing | Static compilation; no dict-shaped scope |
| `__import__` | Missing | Imports resolve at compile time |
| `breakpoint`, `help` | Missing | N/A |

**Exceptions**

Exception hierarchy support. Most built-in exception types raised by
runtime checks are now user-catchable TPy types. Sites that remain panics
are deliberate (internal invariants, OOM, fixed-int arithmetic overflow);
see `docs/EXCEPTION_DESIGN.md` for the per-type migration table and the
helper-API surface.

| Item | Status | Notes |
|---|---|---|
| `BaseException`, `Exception` | Done | Two-tier exception model |
| `ValueError` | Done | Catchable. Migrated runtime sites: `list.remove`/`list.index`, `bytearray.remove`, `bytes` value/`negative count`, `str.split`/`bytes.split` empty separator, `str.index`/`str.rindex` substring not found, `float()`/`int()`/`Int*()` parse errors, `slice` step==0, extended-slice assignment size mismatch, `range()` arg-3 zero, `time.sleep` negative, fixed-int + BigInt negative shift, `int(float('nan'))`, `open()` invalid mode, `from_range` size mismatch, codegen-side `__len__()` negative + enum `from_value` invalid value |
| `OSError`, `FileNotFoundError` | Done | `OSError` also raised by `read()`/`write()`/`flush()`/`readline()`/`readlines()` when the file is opened in the wrong mode. CPython's `io.UnsupportedOperation` (diamond OSError+ValueError) is not reproducible since TPy MI doesn't support diamonds; one-sided divergence (`except OSError` works, `except ValueError` doesn't) |
| `StopIteration` | Done | |
| `IndexError` | Done | Catchable. List/array/span/string/bytes/bytearray out-of-range indexing, list/bytearray `pop` empty all throw `IndexError`. Messages match CPython ("list index out of range" etc.) |
| `KeyError` | Partial | Catchable. Dict `__getitem__` / `__delitem__` / `pop`-no-default missing key, set `remove`-missing / `pop`-empty, TypedDict `total=False` field access on absent value, enum `from_name` (`Color["unknown"]`) all throw `KeyError`. CPython's `str(KeyError(k))` reprs the key (`'missing'`); TPy currently returns the catchall message verbatim -- alignment is a v2 follow-up |
| `TypeError` | Done | Catchable. `ord(s)`/`Char(s)` length-1 violation, `cast(T, any_val)` typeid mismatch (with demangled type names), `hash(any_val)` on an unhashable contained type. User code can `raise TypeError(...)` anywhere |
| `AttributeError` | Done | Catchable throw-tier type; raised by user `__getattr__` / `__setattr__` / `__delattr__` bodies. `hasattr` and 3-arg `getattr` wrap the dunder call in try/catch |
| `ArithmeticError` | Done | Base class of `ZeroDivisionError` and `OverflowError`, matching CPython's hierarchy. `except ArithmeticError` catches either subtype |
| `ZeroDivisionError` | Done | Catchable. Float `/`/`//`/`%`, fixed-int `//`/`%`, BigInt `//`/`%`, `divmod`. Messages match CPython per operation (`float division by zero`, `integer modulo by zero`, etc.) |
| `OverflowError` | Done (catchable) + fixed-int overflow stays panic | Catchable: `int(float('inf'))`, BigInt `2 ** huge_value`, user `raise OverflowError`. Fixed-int arithmetic overflow (Int8..Int64, UInt8..UInt64 add/sub/mul/neg/shift/pow/cast/divmod/round) routes through `raise_fixedint_overflow` -- currently panics, future build/module/function-scope policy switch (`action=none/panic/throw`) plugs in without rewriting call sites |
| `AssertionError` | Done | `assert` failure throws `AssertionError(msg)`; catchable via `try/except` |
| `RuntimeError` | Done (class-only) | Class exposed for `raise RuntimeError(...)` in user code; no runtime panic sites migrated |
| `NotImplementedError` | Done (class-only) | Class exposed for `raise NotImplementedError(...)` |
| `MemoryError` | Done (class-only) | Class exposed; the BigInt OOM panic deliberately stays panic (catching MemoryError is fragile) |
| `FloatingPointError`, `RecursionError` | Done (class-only) | Classes exposed for user `raise`; no runtime sites raise them (inherit `ArithmeticError` / `RuntimeError` per CPython) |
| `LookupError` | Done | Base of `IndexError`/`KeyError`, matching CPython's hierarchy. `except LookupError` catches either subtype |
| `NameError`, `UnboundLocalError` | Not applicable | Compile-time concerns |
| `ImportError`, `ModuleNotFoundError` | Not applicable | Import failures are compile-time today |
| `UnicodeError` and subtypes | Missing | TPy has few encoding-panic sites today |
| `GeneratorExit` | Done | Inherits `BaseException` directly (CPython hierarchy); raisable/catchable, and constructed by the frame destructor as `__exit__`'s exc_val when an abandoned generator/coroutine closes a suspended `with` region |
| `KeyboardInterrupt` | Done | Inherits `BaseException` directly (CPython hierarchy); raisable/catchable, and raised by `asyncio.run` after a SIGINT-driven graceful shutdown |
| `SystemExit` | Missing | Control-flow exception; needs runtime support |
| `SystemError` | Missing | Internal-interpreter notion not directly applicable |
| `EOFError` | Done (class-only) | Class exposed for user `raise`; no runtime sites raise it yet |
| `PermissionError`, `FileExistsError`, `NotADirectoryError`, `IsADirectoryError` | Done | `OSError` subclasses (per CPython). Raised by the `os` syscall errno table (EACCES/EPERM, EEXIST, ENOTDIR, EISDIR); also user-raisable |
| `TimeoutError` | Done | Built-in re-export of `tpy::TimeoutError` (inherits `OSError`, matching CPython where `socket.timeout is TimeoutError`); raised by `asyncio.wait_for` and on a `socket` timeout-mode expiry |

**Sentinels**

| Item | Status | Notes |
|---|---|---|
| `True`, `False` | Done | |
| `None` | Done | |
| `NotImplemented` | Missing | Used by `__eq__` etc. to signal "try the reflected op"; TPy's overload dispatch handles this differently |
| `Ellipsis` (`...`) | Partial | Usable in stub bodies (`def f(): ...`); not a first-class runtime value |

Tests: `tests/cases/builtins/` has a broad suite covering the working
surface (enumerate, filter, map, zip, sorted, hash, abs, bin, hex, oct,
all, any, sum, divmod, range, and many more).

### math

Current: `lib/tpy/math.py` -- native C++ wrappers. Sufficient for numerics-heavy code.

| Item | Status | Notes |
|---|---|---|
| `pi`, `tau`, `e`, `inf`, `nan` | Done | Constants as `Final[float]`. `nan` is `Final[float] = float("nan")` -- the `float(str)` literal forms (`"nan"`, `"inf"`, `"-inf"`, plus case/whitespace variants) fold at codegen to constexpr `std::numeric_limits<double>::quiet_NaN()` / `::infinity()`, bypassing the non-constexpr `tpy::float_from_str` runtime |
| `log`, `log10`, `log2` | Done | `log(x, base)` is pure-TPy overload |
| `log1p`, `expm1` | Done | Thin `std::log1p` / `std::expm1` |
| `sqrt`, `cbrt`, `pow`, `exp`, `exp2` | Done | `cbrt` / `exp2` are Python 3.11+ |
| `floor`, `ceil`, `trunc` | Done | Return `int` (BigInt) / generic `T` |
| `sin`, `cos`, `tan` | Done | |
| `asin`, `acos`, `atan`, `atan2` | Done | |
| `sinh`, `cosh`, `tanh` | Done | |
| `asinh`, `acosh`, `atanh` | Done | |
| `fabs` | Done | |
| `hypot` | Done | Variadic `hypot(*coords)`; internal `_hypot2` native binding to `std::hypot` + overflow-safe hypot-fold |
| `radians`, `degrees` | Done | Pure-TPy |
| `isnan`, `isinf`, `isfinite` | Done | Thin `std::isnan` / `std::isinf` / `std::isfinite` |
| `copysign` | Done | |
| `fmod`, `remainder` | Done | C fmod semantics (truncation); IEEE remainder (nearest-even) |
| `nextafter`, `ldexp`, `fma` | Done | Thin natives; `fma` is CPython 3.13+ (cpy test is no_cpython) |
| `ulp` | Done | `tpy::stdlib::math::ulp` helper matching CPython edge cases for nan/inf/0 |
| `modf` | Done | `tpy::stdlib::math::modf` wrapper returning `std::tuple<double, double>` |
| `frexp` | Done | Generic over the exponent type: `frexp[T](x) -> tuple[float, T]`. Default T is `DefaultInt` (Int32 under default config); users can pick `Int64` or `int` (BigInt) for wider ranges |
| `gcd`, `lcm` | Done | Variadic `gcd(*ints)` / `lcm(*ints)` over BigInt. Internal `_gcd2` binary helper; `lcm` uses `(a // gcd(a,b)) * b` to keep the intermediate bounded by `max(|a|, |b|)`. Generic-over-int-type is a follow-up (see math.py header) |
| `factorial` | Done | Pure-TPy over BigInt; raises `ValueError` on negative |
| `isqrt` | Done | Pure-TPy Newton's method over BigInt; initial guess from `bit_length()` for O(log log n) iteration count |
| `perm`, `comb` | Done | Pure-TPy over BigInt. `perm` has both one-arg (`perm(n) == factorial(n)`) and two-arg overloads via `@overload`. `comb` is binary only |
| `isclose` | Done | Pure-TPy; `rel_tol` / `abs_tol` are kw-only to match CPython |
| `prod` | Done | Pure-TPy; `start` is kw-only to match CPython. Three overloads: `Iterable[Int32]` -> Int32 (fast path), `Iterable[int]` -> int (exact BigInt for arbitrary-precision products), `Iterable[float]` -> float. Kwarg disambiguation lets `prod(empty, start=1.0)` / `start=int(1)` / `start=Int32(1)` pick the right family |
| `fsum` | Done | Pure-TPy Neumaier compensated summation. Takes `Iterable[float]` |
| `sumprod` | Done | Pure-TPy; raises `ValueError` on length mismatch via iterator lockstep drive (mirrors CPython's `zip(..., strict=True)`). Takes `Iterable[float]` |
| `dist` | Done | Pure-TPy Euclidean distance via hypot-fold (overflow-safe for coordinates up to `DBL_MAX`). Takes `Iterable[float]` |
| `gamma`, `lgamma`, `erf`, `erfc` | Done | Thin natives (`std::tgamma` etc.) |

Tests: `math_module`, `math_extended`, `math_log_base`, `math_hyperbolic`,
`math_numeric`, `math_special`, `math_fma`, `math_frexp_generic`,
`math_variadic`, `math_iterable`, `panic_sumprod_mismatch` in
`tests/cases/builtins/`; `float_special_values` in `tests/cases/float/`
(covers `float("nan"/"inf"/"-inf")` fold).

**Remaining gaps to reach 100%:**
- Tuples as `Iterable[T]`. Tuples don't iterate today in TPy regardless of protocol context -- real CPython compat gap but bundled scope (needs coordinated sema + codegen + runtime story, not just a conformance flag flip). See TODO.md.

### time

Current: `lib/tpy/time.py` -- native_module. Wall-clock + monotonic +
process-CPU clocks shipped; calendar / formatting surface deferred.

| Item | Status | Notes |
|---|---|---|
| `time()` | Done | Seconds since epoch as float |
| `sleep(s)` | Done | |
| `perf_counter()` / `monotonic()` | Done | `std::chrono::steady_clock`. CPython's `perf_counter` and `monotonic` share the same underlying clock on POSIX; we mirror that |
| `perf_counter_ns()` / `monotonic_ns()` / `time_ns()` | Done | Return `Int64`; epoch reasonable through year 2262 (INT64_MAX ns) |
| `process_time()` | Done | `std::clock() / CLOCKS_PER_SEC`. CPU time, ~1us resolution on Linux glibc (CPython uses `clock_gettime(CLOCK_PROCESS_CPUTIME_ID)` for ns precision -- a future tightening) |
| `sleep_until_steady(deadline)` | Done | TPy extension (no CPython equivalent). Sleeps until the given `monotonic()`-domain deadline; used by `asyncio`'s timer-heap drain |
| `struct_time` | Missing | Needs named-tuple-like or @dataclass |
| `gmtime`, `localtime` | Missing | Depends on struct_time |
| `strftime`, `strptime` | Missing | Formatting strings; depends on struct_time |
| `mktime` | Missing | Depends on struct_time |
| `asctime`, `ctime` | Missing | Depends on struct_time |
| `timezone`, `altzone`, `tzname` | Missing | Module-level constants |

Tests: `time_module`, `time_sleep`, `time_import`, `stdlib/time_clocks`
(invariants on perf_counter / monotonic / process_time / time_ns since
absolute timing values are non-deterministic).

### sys

Current: `lib/tpy/sys.py` -- native; `argv`, `stdout`, `stderr`, `exit`,
`maxsize`, `byteorder`, `maxunicode`.

| Item | Status | Notes |
|---|---|---|
| `argv` | Done | List populated at runtime init |
| `stdout`, `stderr` | Done | Backed by `tpy::StdStream` (wraps `std::cout` / `std::cerr`); satisfy the `Writable` protocol so they work as `print(file=...)` targets and expose `write(str) -> Int32` / `flush()` |
| `stdin` | Missing | Needs read-side protocol; lower priority than write |
| `exit(code)` | Done | `Int32` arg lowered to `std::exit(int)` via `tpy::sys_exit`; `[[noreturn]]` |
| `platform` | Missing | Compile-time constant |
| `version`, `version_info` | Missing | Already in `tpy.version`; could re-export |
| `path` | Missing | List; relates to import machinery (TPy resolves at compile time, so semantics differ) |
| `modules` | Missing | Not meaningful under static compilation |
| `maxsize` | Done | Pure-TPy `int` constant, CPython's 64-bit value (`2**63 - 1`) |
| `byteorder` | Done | Pure-TPy `str` constant `"little"` (TPy targets little-endian x86-64 / ARM64) |
| `maxunicode` | Done | Pure-TPy `int` constant `1114111` (U+10FFFF, target-independent) |
| `getsizeof` | Missing | Hard: sizes differ from CPython (inline fields vs boxed) |
| `executable` | Missing | `argv[0]` / `/proc/self/exe` |

Tests: `sys_argv`, `sys_maxsize`, `sys_byteorder`, `kwargs_print_file_std`.

### os

**Partial.** Filesystem queries over `std::filesystem` and mutating ops over
raw POSIX syscalls (helpers in `runtime/cpp/include/tpy/stdlib/os.hpp`, thin
`@native` facade in `lib/tpy/os/__init__.py`). Syscall failures route through the
shared PEP 3151 table (`tpy::raise_mapped_os_error` in core.hpp) mapping each
errno to the OSError subclass CPython raises (`ENOENT`->`FileNotFoundError`,
`EEXIST`->`FileExistsError`, `ENOTDIR`->`NotADirectoryError`,
`EISDIR`->`IsADirectoryError`, `EACCES`/`EPERM`->`PermissionError`, the
connection errnos -> the `ConnectionError` family, `ETIMEDOUT`->`TimeoutError`,
else `OSError`), populating `.errno`/`.strerror`/`.filename` (and `.filename2`
for `rename`/`replace`/`link`/`symlink`) with CPython-exact `str(e)` text. `stat`/`lstat` return a
`stat_result` over the same layer. `os.environ` is a snapshot mapping captured
at program start (a `_Environ` singleton in the leaf `os/_environ.py`); writes
through it (`environ[k]=v` / `del environ[k]`) sync libc via `setenv`/`unsetenv`,
while `getenv`/`expandvars` read the snapshot -- matching CPython, where
`os.putenv`/`os.unsetenv` are libc-only and leave the mapping stale. Process
spawning stays blocked.

| Item | Status | Notes |
|---|---|---|
| `getcwd`, `chdir` | Done | `std::filesystem::current_path` get/set |
| `listdir` | Done | `std::filesystem::directory_iterator`; basenames, unspecified order (like CPython). `listdir()` defaults to the cwd (`path="."`) |
| `scandir` | Done | `readdir`-backed; returns an eager `list[DirEntry]` (NOT CPython's lazy iterator / context manager -- `with os.scandir()` is a compile error, not silent). `DirEntry` has `name`/`path` + `is_dir`/`is_file`/`is_symlink`/`stat`; uses the readdir `d_type` fast path with a `stat` fallback for symlinks/unknown (is_dir/is_file follow symlinks; is_symlink uses lstat). `DirEntry.stat()` returns a fresh `stat_result` (no caching yet) and `follow_symlinks=False` variants are deferred -- see TODO.md |
| `getenv` | Done | Two overloads (typeshed-style): `getenv(key)` -> `str | None`, `getenv(key, default: str)` -> `str` (so `len(os.getenv(k, ""))` type-checks). Reads the `os.environ` snapshot (CPython's `getenv` is `environ.get`), not live libc |
| `mkdir`, `rmdir`, `remove`, `rename`, `symlink` | Done | Raw `::mkdir`/`::rmdir`/`::unlink`/`::rename`/`::symlink`; byte-exact CPython errno parity |
| `makedirs` | Done | Pure-TPy recursion over `mkdir`; `mode=`/`exist_ok=` supported |
| `mkdir(mode=)` | Done | `::mkdir(path, mode)`; mode is umask-masked like CPython |
| `replace` | Done | POSIX `rename(2)` (same as `rename`; the cross-platform-overwrite guarantee is the only difference) |
| `removedirs` | Done | Pure-TPy prune of empty parents via `dirname` (avoids a `split`-tuple-unpack dangling bug -- see BUGS.md) |
| `readlink` | Done | `::readlink` with a grow-on-truncation buffer |
| `stat`, `lstat` | Done | `::stat`/`::lstat` -> a pure-TPy `stat_result` (native returns a flat 13-tuple, wrapped TPy-side). Fields: `st_mode`/`st_ino`/`st_dev`/`st_nlink`/`st_uid`/`st_gid`/`st_size` (Int64), `st_atime`/`st_mtime`/`st_ctime` (float), `st_*_ns` (Int64). `st_blksize`/`st_blocks`/`st_rdev` deferred |
| `open`, `close`, `read`, `write`, `lseek`, `pipe`, `dup`, `dup2`, `fstat` | Done | Low-level fd I/O over raw POSIX; `read`/`write` move `bytes` (`vector<uint8_t>`/`span`). EAGAIN/EWOULDBLOCK on a would-block fd raises `BlockingIOError` (CPython parity; lets `socket.makefile`'s reader remap a recv-timeout to `TimeoutError`). `O_RDONLY`..`O_APPEND` + `SEEK_SET`/`CUR`/`END` exposed (see note below). `O_NONBLOCK`/`O_CLOEXEC`, `openat`/`dir_fd`, `get`/`set_blocking` deferred |
| `chmod`, `chown`, `utime`, `access` | Done | Raw POSIX (`chmod`/`chown`/`utimensat`/`access`); `access` takes `F_OK`/`R_OK`/`W_OK`/`X_OK` and returns bool. `utime` takes a `(atime, mtime)` tuple; the no-arg (now) and `ns=` forms are deferred. `dir_fd`/`follow_symlinks` deferred |
| `urandom` | Done | `getentropy` in 256-byte chunks |
| `getpid`, `getppid`, `getuid`, `geteuid`, `getgid`, `getegid`, `getlogin`, `umask`, `cpu_count`, `strerror`, `isatty`, `unlink` | Done | Thin POSIX binds. `cpu_count` -> `int | None` (None when indeterminate); `getlogin` raises OSError on failure; `unlink` aliases `remove`. `getgroups`/`setuid`/`getpgid`/`nice` and the rest of the credentials/scheduling surface deferred |
| `link`, `truncate`, `ftruncate`, `fsync` | Done | Raw POSIX (`::link`/`::truncate`/`::ftruncate`/`::fsync`); `dir_fd`/`follow_symlinks` on `link` deferred |
| `fdatasync` | Deferred | Linux-only in libc (no macOS equivalent; CPython omits it on macOS); re-add when source-level platform-conditional support lands (see FEATURE_ROADMAP H3) |
| `get_terminal_size` | Done | `ioctl(TIOCGWINSZ)` -> `terminal_size` (`columns`/`lines`; attribute-only, no tuple unpack -- like `stat_result`); raises OSError on a non-tty fd (matches CPython) |
| `fspath` | Done | str identity; the PathLike (`__fspath__`) form arrives with `pathlib` |
| `environ.pop`/`setdefault`/`update`/`clear`/`copy` | Done | `pop`/`setdefault` take a required `default: str` (single signature -- cross-module method overloads don't resolve, see BUGS.md; bare `pop(key)`-raises deferred). `update(dict)`, `clear()`, `copy() -> dict[str,str]` |
| `name`, `sep`, `extsep`, `pathsep`, `linesep`, `curdir`, `pardir`, `devnull` | Done | Module-level POSIX constants (`name` = `"posix"`). `altsep` omitted (`None` on POSIX) |
| flag/mode constants (`O_*`, `SEEK_*`, `*_OK`) | Done | Their names are libc macros, so they can't be emitted as C++ symbols; each binds via `native_global` to a safe-named C++ global holding the real macro value -- correct on every platform (the `O_CREAT` family differs Linux/macOS) |
| `environ`, `putenv`, `unsetenv` | Done | `environ` is a snapshot `_Environ` mapping frozen at program start: `[]` get/set/del, `in`, `.get`, `len`, `for k in environ`, `keys`/`values`/`items`. `getenv`/`expandvars` read it. `putenv`/`unsetenv` are libc-only and do NOT update the snapshot (CPython's footgun). Two intentional narrowings: `.get` is a single `get(key, default=None)` not the typeshed overload pair (cross-module method overloads don't resolve -- see BUGS.md); `keys`/`values`/`items` return owned snapshot `list`s, not CPython's live set-like views (iteration is identical; set ops / live reflection unsupported) |
| `path` | Partial | The [`os.path`](#ospath) submodule |
| `walk` | Working | `os.walk(top, topdown=True, onerror=None, followlinks=False)` -- an iterative (explicit-stack; `yield from` is unsupported) generator yielding `(dirpath, dirnames, filenames)`, built on `scandir`/`os.path.join`/`os.path.islink`. **Topdown**: in-place `dirnames` pruning works (the yielded list aliases the generator frame), pre-order. **Bottomup (`topdown=False`)**: post-order via a two-marker explicit stack (`_WalkExpand` to scan / `_WalkEmit` once the subtree is yielded), discriminated by `isinstance` on the popped `_WalkExpand | _WalkEmit` union; yields fresh `dirnames`/`filenames` copies (no prune contract -- editing them is a no-op, matching CPython, since the descent already happened). `followlinks` honored in both modes; an unscannable directory is reported to `onerror` (a `Callable[[readonly[OSError]], None]`; the `readonly` is a workaround for a slicing-guard over-rejection -- BUGS.md -- so a named callback must annotate `readonly[OSError]` for now, though a lambda works unannotated; drop it once the guard is fixed) or silently skipped when `onerror=None` (default), matching CPython; an `onerror` that raises aborts the walk. One narrowing vs CPython: `scandir` is eager (it materializes the full `list[DirEntry]` before `walk` sees it), so a mid-iteration read error -- CPython's second `onerror` call path while advancing the scandir iterator -- cannot reach `onerror`; only the open/scandir-failure path does. Practically unreachable on normal filesystems. |
| `fork`, `exec*`, `spawnv*`, `system` | Blocked | Process spawning |

### os.path

**Partial.** `lib/tpy/os/path.py` -- POSIX (`posixpath`) surface: pure-TPy
string manipulation plus a filesystem-query tier backed by `std::filesystem`
(shared with `os`, via `tpy/stdlib/os.hpp`). TPy targets little-endian
Linux/ARM, so this is posix-only (no `ntpath`): `splitdrive` always returns
`("", p)`, `sep` is `/`. The cpy test phase runs the cases against CPython's
real `posixpath`, so the shipped functions are byte-parity-verified.

Imports: `from os.path import join`, `import os.path`, `import os` (then
`os.path.join`), and `from os import path` all work.

| Item | Status | Notes |
|---|---|---|
| `join` | Done | Variadic `join(path, *paths)`; absolute component resets, trailing-slash handling matches CPython |
| `split`, `dirname`, `basename` | Done | Trailing-slash / all-slash head rules match `posixpath` |
| `splitext` | Done | Leading-dot basenames (`.bashrc`) have no extension; splits at last dot |
| `isabs` | Done | `p.startswith("/")` |
| `splitdrive` | Done | POSIX: always `("", p)` |
| `normpath` | Done | Dot/dotdot collapse; two leading slashes preserved, three-plus collapse (POSIX) |
| `commonprefix` | Done | Character-level (not path-aware) -- matches CPython's documented quirk |
| `sep`, `extsep`, `pardir`, `curdir`, `pathsep`, `defpath`, `devnull` | Done | Module constants (`altsep` omitted -- `None` on POSIX, no clean str-or-None constant) |
| `exists`, `lexists`, `isfile`, `isdir`, `islink` | Done | `std::filesystem` status queries; swallow OS errors and return False (matches CPython). `islink`/`lexists` symlink-True paths await `os.symlink` (M2) for test coverage |
| `getsize` | Done | `stat().st_size` (follows symlinks; returns a directory's size like CPython, not just regular files); missing path raises `FileNotFoundError`. Returns `Int64` (CPython `int`) -- same printed value |
| `abspath` | Done | `os.getcwd`-relative + `normpath`; absolute input is just `normpath` |
| `getmtime`, `getatime`, `getctime` | Done | Native `stat`-backed float helpers (parallel to `getsize`); tests assert invariants (machine-dependent values) |
| `samefile` | Done | Native helper comparing `st_ino`/`st_dev` of two paths |
| `ismount` | Done | Native helper; `st_dev` differs from parent, or parent is self (root) |
| `commonpath` | Done | Pure-TPy: split each path on `/` (dropping empty and `.` components), component-wise common prefix via an explicit loop (no `min`/`max` over lists needed). Empty list or a mix of absolute and relative paths raises `ValueError`, matching CPython |
| `normcase` | Done | POSIX identity (returns the path unchanged); case-folding/separator-normalization is Windows-only |
| `expandvars` | Done | Pure-TPy `$name`/`${name}` expansion; unset names / bare `$` / unclosed brace left verbatim. Reads the `os.environ` snapshot (matching CPython); a bare `os.putenv` is not observable through it |
| `realpath`, `relpath` | Done | `realpath` via `std::filesystem::weakly_canonical` over an absolutized path (resolves symlinks in the existing prefix, lexically normalizes a non-existent tail, never fails on a missing path); a filesystem error (symlink loop / permission) falls back to the lexical absolute path -- a possible byte-divergence from CPython's partial best-effort on a loop (rare; see TODO.md). `realpath(path, strict=True)` uses `std::filesystem::canonical` instead (the whole path must exist) and raises the matching OSError subclass (FileNotFoundError on a missing component). `relpath(path, start=".")` is pure-TPy over `abspath` + component common-prefix |
| `expanduser` | Done | `~`/`~/...` from the `os.environ` `HOME` snapshot (falls back to the current user's pwd home when unset); `~user`/`~user/...` via a `getpwnam` pwd binding. Unresolvable `~`/unknown `~user` left verbatim. Full `pwd` module (struct_passwd) still deferred -- expanduser uses an internal home-dir helper |
| `samestat` | Done | `s1.st_ino == s2.st_ino and s1.st_dev == s2.st_dev`. `stat_result` moved to the type-only `os/_types` leaf so `os.path` imports it without an executable-bearing cyclic import of `os` |

Tests: `tests/cases/stdlib/ospath_strings` (string surface + constants),
`ospath_normpath`, `ospath_commonprefix` (also covers the `import os` /
`from os import path` forms), `ospath_fs` (exists/lexists/isfile/isdir/islink/
getsize/abspath), `ospath_fs_errors` (FileNotFoundError mapping),
`os_fs` (getcwd/chdir/listdir/getenv), `os_mutate`/`os_mutate_errors`
(mutating ops + errno subclasses), `os_stat`/`os_stat_errors`
(stat/lstat + stat queries), `os_getenv_expandvars` (getenv overloads +
expandvars), `os_environ`/`os_environ_iter`/`os_environ_methods`
(mapping surface), `os_scandir`/`os_listdir_default`, `os_fd_io`/`os_fd_errors`
(fd I/O + error paths), `os_metadata`/`os_fileops` (chmod/chown/utime/access +
link/truncate/fsync), `os_urandom`, `os_sysinfo`, `os_get_terminal_size`,
`ospath_expanduser`/`ospath_realpath_relpath`, `ospath_commonpath`,
`ospath_roundout` (normcase/samestat/realpath strict=). All cpy-phase byte-compared against
CPython; filesystem cases `chdir` to a known dir and create fixed `/tmp`
fixtures for determinism.

### pathlib

**Missing.** Class-based wrapper around `os.path`; depends on filesystem bindings.

| Item | Status |
|---|---|
| `Path`, `PurePath` | Missing |
| Path operators (`/`), `.parent`, `.name`, `.suffix`, `.stem` | Missing |
| `.read_text`, `.write_text`, `.read_bytes`, `.write_bytes` | Missing |
| `.glob`, `.rglob`, `.iterdir` | Missing |
| `.exists`, `.is_file`, `.is_dir`, `.stat`, `.unlink`, `.mkdir` | Missing |

### io

**Partial.** v1 ships `io.StringIO` + `io.BytesIO`, the raw `io.FileIO` (fd
adopt) + `io.BufferedReader` binary-read layer (the buffered reader
`socket.makefile()` and `http.client` build on), plus six IO protocols
(`Readable`/`Writable`/`BinaryReadable`/`BinaryWritable`/`Seekable`/`Closable`)
that consumer code parameterizes over (`def f(fp: Writable)`). Both buffer
types explicitly inherit the relevant protocols (`StringIO(Writable, Readable,
Seekable, Closable)`), so conformance is documented at the class header.

Storage strategy: `list[str]`/`list[bytes]` chunks. The fast path
(append-at-end, dominant for the "build payload then `getvalue()`" pattern) is
O(1) per write and never joins; mid-buffer overwrites collapse the chunks to
a single contiguous buffer first. `getvalue()` joins lazily and does not
materialize state.

Design choice: no `IOBase` ABC hierarchy. TPy's static dispatch makes the
runtime introspection that `IOBase` exists for in CPython (e.g. `isinstance(fp,
IOBase)`) unnecessary; the protocols give zero-overhead dispatch and cleanly
split text/binary + seek/close concerns. Adding the `IOBase` family later as a
combined protocol layer (`class IOBase(Closable, Seekable, Protocol): ...`)
remains an option without breaking the existing granular surface.

| Item | Status | Notes |
|---|---|---|
| `StringIO(initial="")` | Done | Chunked write/read; `read`/`readline`/`readlines`/`seek`/`tell`/`truncate`/`getvalue`/`flush`/`close`/`__iter__`/`__enter__`/`__exit__` |
| `BytesIO(initial=None)` | Done | Same surface as `StringIO`, returning `bytes` |
| `FileIO(fd, closefd=True)` | Done | Raw unbuffered binary layer over an OS fd (pure TPy over `os.read`/`os.close`). `read(size)` = one `os.read`; `read(-1)` drains to EOF; `readable`/`fileno`/`close`/`closed`/`__enter__`/`__exit__`. `@nocopy`; `closefd=False` reads an fd owned elsewhere. Adopt-fd only (no path constructor); `readinto`/`write` deferred |
| `BufferedReader(raw, buffer_size=...)` | Done | Buffered binary reader over any `@dynamic RawBinaryIO` source (`_raw: Box[RawBinaryIO]`, type-erased via `make_adapter` -- avoids a viral type param up the http stack while still accepting an fd-backed `FileIO` or a userspace reader like `ssl.SSLRawIO`). `read(size=-1)` blocks until `size` or EOF; `readline(size=-1)`, `readlines`, `__iter__`, `close`/`closed`/`readable`/`__enter__`/`__exit__`. Conforms to `BinaryReadable`/`Closable`. Buffer logic mirrors `asyncio.StreamReader`. `peek`/`readinto` and text mode deferred (see TODO.md) |
| `DEFAULT_BUFFER_SIZE` | Done | `8192`, matching CPython |
| `Writable` / `Readable` / `BinaryWritable` / `BinaryReadable` | Done | Protocols on `tpy._core._types`, re-exported from `tpy/__init__.py` and from `io` |
| `Seekable` / `Closable` | Done | Same; `Seekable` is `seek(pos, whence=0)` + `tell()`; `Closable` is `close()` only (the `closed` property is left out of the protocol but available on the concrete classes) |
| `seek(pos, whence=0)` | Partial | `whence` is Int32; `io.SEEK_SET/CUR/END` (Int32) now exposed via native_global. `pos`/`tell` are Int32 -- widening to Int64 for >2GB buffer positions is a filed follow-up (see TODO.md) |
| `seek` past end | Diverges | v1 clamps to `_total`; CPython back-fills with NUL/0. Revisit with a real consumer |
| `__iter__` (line-by-line) | Done | Generator method yielding `readline()` results until empty |
| `read(size=-1)` | Done | `size < 0` (default) reads all remaining; `size >= 0` returns at most `size` units and advances accordingly. On `StringIO`/`BytesIO` and the native `TextIO`/`BinaryIO` file objects, plus the `Readable`/`BinaryReadable` protocols. Byte-counted (codepoint vs byte divergence for non-ASCII -- TPy-wide str indexing, not specific to read) |
| `IOBase` / `RawIOBase` / `BufferedIOBase` / `TextIOBase` | Missing (deferred) | Not planned -- protocols cover the static-dispatch use case |
| `TextIOWrapper` | Missing | Encoding + newline translation over a binary buffer. Not a prerequisite for json/csv/configparser (those operate over text file objects). Needs a buffer-ownership design call (see TODO.md); defer until a consumer demands it |
| `encoding=` / `newline=` / `errors=` kwargs | Missing | v1 doesn't translate text |
| `UnsupportedOperation` | Missing | Defer until a method needs to raise it (e.g. seek on a non-seekable wrapper) |
| `open()` | Done | In `builtins` (not in `io`); exposes `TextIO`/`BinaryIO` |

Tests: `cases/stdlib/io_stringio_basic`, `cases/stdlib/io_bytesio_basic`,
`cases/stdlib/io_protocols` (consumer functions parameterized over the four
protocols, both `StringIO`/`BytesIO` and pass-through wiring),
`cases/stdlib/io_read_size` (`read(size)` on both buffers, the protocol
params, and the native file objects), `cases/stdlib/io_seek` (SEEK_* constants
+ StringIO.seek), `cases/stdlib/io_fileio` (raw fd read/closefd over a pipe),
`cases/stdlib/io_bufferedreader` (readline/read/multi-fill/readline-cap/iter
over a pipe-backed FileIO).

### json

**Partial.** `lib/tpy/json.py` is a pure-TPy wrapper over `tplib.json`'s
`JsonReader` / `JsonWriter`. Untyped values are represented by a recursive
union alias `JsonValue = None | bool | int | float | str | list[JsonValue] |
dict[str, JsonValue]` -- ints stay BigInt, floats stay double, no precision
loss. CPython byte-compatible output for the `loads` / `dumps` surface across
all primitive types and nested containers (verified via the cpy phase in
`tests/cases/stdlib/json_*`). For typed deserialization into user records,
`tplib.json` with the `@model` decorator remains faster.

| Item | Status | Notes |
|---|---|---|
| `loads(s)` | Done | Returns `Own[JsonValue]`. Raises `JSONDecodeError` on malformed input or trailing data. Consume inline -- the result auto-moves into an annotated local (`d: JsonValue = json.loads(s)`) and a bare-generic `isinstance(d, dict)` / `list` narrows it to that member; no helper extraction needed. |
| `dumps(obj, *, indent, sort_keys)` | Done | `indent` and `sort_keys` kwargs supported. CPython byte-compatible for ASCII. Raw list/dict literals (incl. empty `[]`/`{}`, `None` elements, and arbitrary nesting) serialize without importing `JsonValue`. A concrete `list`/`dict` *variable* (e.g. `dict[str, Int32]`) is **intentionally not** implicitly converted -- that would be a silent O(n) deep copy into the wrapper representation; the compiler rejects it with a diagnostic (build as `d: JsonValue = {...}` or pass a literal). The zero-copy fix is in-place serialization of typed shapes (planned -- see TODO.md). Known input gap (see BUGS.md): a `tuple` arg is rejected. |
| `JSONDecodeError` | Done | Subclasses `ValueError` (matches CPython). Thrown via normal `try`/`except`. Carries `msg`, `doc`, `pos`, `lineno`, `colno`. |
| `load(fp)` | Done | `load(fp: Readable)` == `loads(fp.read())`. Accepts any text file object (`io.StringIO`, `open()`'s `TextIO`). |
| `dump(obj, fp)` | Done | `dump(obj, fp: Writable, *, indent, sort_keys)` == `fp.write(dumps(obj, ...))`. |
| `JSONEncoder`, `JSONDecoder` | Missing | Extension hooks; not yet implemented |
| `dumps` kwargs `ensure_ascii`, `separators`, `allow_nan`, `default`, `cls`, `skipkeys` | Missing | Current behavior is `ensure_ascii=False` (raw UTF-8) with CPython default separators |
| `loads` kwargs `object_hook`, `object_pairs_hook`, `parse_float`, `parse_int`, `parse_constant` | Missing | -- |

### re

**Partial.** Pure-TPy facade in `lib/tpy/re.py` over raw PCRE2 bindings in
`lib/tpy/_bindings/pcre2.py`. PCRE2 is vendored at
`runtime/cpp/third_party/pcre2/` (10.44, ~5MB after stripping `doc/`,
`testdata/`, and autotools build files; reproducible via
`scripts/vendor_pcre2.py`) and built into the user binary via the existing
`BuildLayout.build_cpp_commands` infrastructure -- no separate CMake
required for `tpyc -x` / `-b`. Backend selection: `tpyc --pcre2=bundled`
(default), `--pcre2=system` (`-lpcre2-8`), or `--pcre2=auto`. The CMake-
emit path emits a 3-mode selector into `sources.cmake` so users
integrating into a larger CMake project can flip via `-DTPY_PCRE2=system`.

History: F8 was originally listed as a prerequisite to choose between
`std::regex` and PCRE2 backends. Reframed as a future enhancement -- v1
ships PCRE2 only (CPython-quality semantics, mature JIT). cppstd backend
deferred until embedded targets actually need it.

Architecture (no C++ wrapper layer, no pcre2.h in TPy-generated TUs):

  * `runtime/cpp/include/tpy/stdlib/pcre2_h.hpp` -- hand-written facade
    that mirrors the PCRE2 symbols and types we use (opaque struct
    forward-decls, `extern "C"` function declarations, `PCRE2_SPTR8` /
    `PCRE2_UCHAR8` / `PCRE2_SIZE` typedefs). Deliberately does NOT
    `#include <pcre2.h>` -- pcre2.h's `PCRE2_*` macros would otherwise
    collide with TPy module-level constants of the same name. The vendored
    PCRE2 .c files include the real pcre2.h during their separate
    compilation; the linker resolves our extern "C" declarations to those
    symbols.
  * `lib/tpy/_bindings/pcre2.py` -- pure `@native` 1:1 bindings to `pcre2_*_8`
    C primitives. Mirrors upstream constant names exactly
    (`PCRE2_CASELESS`, `PCRE2_SUBSTITUTE_GLOBAL`, etc.). No Python semantics.
  * `lib/tpy/re.py` -- facade. Pattern + Match classes manage
    `pcre2_code*` / `pcre2_match_data*` lifetimes via `__del__`. All flag
    mapping, group accessors, sub/split/findall logic live here in TPy.

| Item | Status | Notes |
|---|---|---|
| `compile`, `Pattern` | Done | Pure-TPy class wrapping `Ptr[pcre2.Code]`; JIT-compiled on construct |
| `search`, `match`, `fullmatch` | Done | Return `Optional[Own[Match]]` |
| `findall` | Done | List of group-0 strings. Doesn't yet return captures-tuples for grouped patterns (CPython divergence) |
| `finditer` | Done | Lazy generator yielding `Own[Match]` (like CPython) |
| `sub`, `sub(count=)` | Partial | `count` limits replacements (0 = all, negative = none), matching CPython including empty-match advancement. Backref syntax is PCRE2-native (`$1`, `${name}`), not CPython's `\1` -- syntax translator deferred |
| `split`, `split(maxsplit=)` | Done | Driven off `finditer`; zero-width patterns and multibyte (UTF-8) input split CPython-identically |
| `Match.group(int)`, `start`, `end`, `span` | Done | All returning `Int32` offsets and TPy `str` slices |
| `Match.groups()` | Partial | Returns `list[str]` instead of tuple (varadic-tuple support pending) |
| `Match.group("name")`, `groupdict` | Missing | Needs PCRE2 nametable walk |
| Flags (`IGNORECASE`, `MULTILINE`, `DOTALL`, `VERBOSE`, `ASCII`) | Done | CPython bit values; mapped to upstream `PCRE2_*` flags. `re.A` / `re.I` / `re.M` / `re.S` / `re.X` aliases too |
| `re.error` | Done | Catchable exception subtype |
| Named groups `(?P<name>...)` | Done (syntax) | PCRE2 accepts Python's `(?P<name>...)` form natively for back-compat |
| Backreferences `(?P=name)` in pattern | Done | PCRE2-native |
| Backreferences in `sub` replacement | Partial | PCRE2 `$1` syntax only; CPython's `\1` needs a translator (deferred) |
| Lookahead, lookbehind, `\p{...}`, etc. | Done | All PCRE2 features available -- patterns just work |
| `re.compile` cache | Missing | Needs module-level mutable state (see "stdlib enablement workstream" above) |
| bytes input | Missing | str only for now |

Tests:
  * `cases/stdlib/re_basic/` -- CPython-compatible surface. TPy and
    CPython produce byte-identical output for compile/search/match/
    fullmatch/findall/split/sub-without-backref + all 5 flags + pattern
    features (quantifiers, classes, anchors, backrefs in pattern,
    non-capturing groups) + error class catching. cpy phase enabled.
  * `cases/stdlib/re_pcre2_specific/` -- divergent behaviors isolated:
    sub with PCRE2 `$1`/`$2` backref syntax, `Match.groups()` returning
    `list[str]` vs CPython's `tuple[str, ...]`. Marked `no_cpython`.
    Each divergence is tracked as a `TODO(v2)` in `lib/tpy/re.py`; when
    the syntax translator + varadic-tuple support land, this test folds
    into `re_basic/`.

### collections

**Partial** -- `Counter` v1 shipped (pure TPy over `dict[T, int]`); the rest is unbuilt.

| Item | Status | Notes |
|---|---|---|
| `OrderedDict` | Missing | TPy already uses `tpy::ordered_map` for `dict[K,V]`; this would be a thin alias or subclass |
| `defaultdict` | Missing | Macro-friendly: store factory, synthesize `__getitem__` |
| `Counter` | Partial | Pure TPy over `dict[T, int]`. v1: construct (empty / from iterable), `c[key]` (missing -> 0), `__setitem__`, `len`, `in`, `total()`, `most_common(n)`, `update`/`subtract`. Deferred (filed compiler blockers, BUGS.md): `elements()` (generator dict-subscript const bug), `+`/`-`/`&`/`|` (field-target subscript-assignment readonly-key bug); also bare `most_common()` and `Counter(mapping/**kwargs)` |
| `deque` | Missing | Needs C++ backing (std::deque) with Python-like API |
| `namedtuple` | Missing | Would be a class macro; could desugar to @dataclass(frozen=True) |
| `ChainMap` | Missing | Pure TPy over list of dicts |
| `abc.*` (Sequence, Mapping, ...) | Partial | Some in `typing`; deeper introspection absent |

### itertools

**Partial.** `lib/tpy/itertools.py` ships seven pure-TPy generators:
`count`, `repeat`, `cycle`, `islice` (single-arg `islice(it, stop)`),
`takewhile`, `dropwhile`, `filterfalse`. No runtime primitives exist --
`runtime/cpp/include/tpy/itertools.hpp` holds only the *builtin* iterator
machinery (`enumerate`, `zip`, `map`, `filter`, `reversed`), so the module is
pure-TPy generators from scratch (the policy-aligned approach). The
lazy-stop functions use generator-loop `break`/`continue`, the predicate
functions take an `Fn` (callable) param, and `islice`/`takewhile` *consume*
another generator (`islice(repeat(...))`, `takewhile(pred, count())`) -- all
of which now work after the generator-loop break/continue, Fn-param,
template-leak, comprehension-iteration, and self-iterator-copy fixes.
`repeat` exposes CPython's two arms via `@overload` -- `repeat(object)`
(unbounded) and `repeat(object, times)` (a count of 0 or negative yields
nothing, matching CPython); the param is named `object` to match CPython's
keyword surface (`repeat(object=...)`), and the impl's `Optional` sentinel stays
private behind the stubs, so `repeat(object, None)` is rejected as CPython
rejects a non-int `times`. Deferred,
each on a distinct compiler gap: `chain` / `product` / `permutations` /
`combinations` (variadic tuples), `compress` (zip /
cross-module-iterator-in-generator), `accumulate` (generic accumulator across
the resumable frame), `pairwise` (`Optional[T]`-across-yield tuple miscompile),
`starmap` (simple-peephole `Iterable`-param dangling capture for the 2-arg
form; variadic form also needs variadic tuples), `tee` / `groupby`
(buffering). Each deferred gap is filed in BUGS.md / TODO.md. The
`islice(start, stop[, step])` form -- now unblocked by the overloaded-generator
fix -- is a future addition (new functionality, via `/tpy-add-feature`).

**Acknowledged divergences from CPython's C `itertools`** (inherent to pure-TPy
generators, which can't alias elements across a `yield` the way the C module
does): `cycle` buffers elements into an internal `list` (copies reference-type
elements; CPython re-yields the same objects), and `repeat` copies its element
at each yield -- so for a *reference-type* element, mutations to the original
are not visible through cycled/repeated values (value-type elements, the common
case, are unaffected). `islice(it, negative)` yields `[]` rather than raising
`ValueError` as CPython does.

| Item | Status | Notes |
|---|---|---|
| `count`, `cycle`, `repeat` | Done | Pure TPy; `repeat` exposes CPython's `repeat(object)` / `repeat(object, times)` via `@overload` (private `Optional` sentinel impl; `repeat(object, None)` rejected) |
| `takewhile`, `dropwhile`, `filterfalse` | Done | Pure TPy; `Fn` predicate param |
| `islice` | Partial | `islice(it, stop)` only; the `(start, stop[, step])` form is future work (overloaded generators now unblocked; needs `/tpy-add-feature`) |
| `starmap` | Missing | 2-arg form blocked on the simple-peephole `Iterable`-param dangling-capture bug (BUGS.md); variadic form also needs variadic tuples |
| `chain`, `chain.from_iterable` | Missing | Blocked on variadic tuples |
| `compress` | Missing | Blocked on zip / cross-module-iterator-in-generator |
| `tee` | Missing | Needs buffering |
| `zip_longest` | Missing | Wrapper |
| `product`, `permutations`, `combinations`, `combinations_with_replacement` | Missing | Blocked on variadic tuples |
| `groupby`, `accumulate`, `pairwise`, `batched` | Missing | `accumulate` (generic accumulator), `pairwise` (`Optional[T]`-across-yield miscompile), `groupby` (buffering), `batched` (needs runtime-sized tuples; would diverge to `list`) |

Key question: whether the module is pure TPy re-exporting C++ generators
or `@native` thin shims. The @native qualification gap that previously
blocked the pure-TPy approach has been resolved (codegen always emits
`::`-qualified names for `@native` refs).

### functools

Current: `lib/tpy/functools.py` -- pure-TPy `reduce` (both 2-arg and 3-arg
forms). Landing the rest is gated on specific compiler fixes tracked in
BUGS.md / TODO.md, not on macro or closure infrastructure:

- **`cmp_to_key`** requires either `copy()` to strip readonly through
  generic `T`, arithmetic on `readonly[FixedInt]`, or an opt-out from the
  unconditional readonly deduction on comparison dunders. See BUGS.md
  "Readonly propagation through generic T blocks storing callable cmp/key".

| Item | Status | Notes |
|---|---|---|
| `reduce(func, a, initial)` | Done | Pure TPy. Takes `Iterable[T]` -- accepts list literals, `range()`, bound list/iter vars |
| `reduce(func, a)` | Done | Pure TPy. Takes `list[T]` (random-access; raises on empty). Restricted to list rather than `Iterable[T]` because TPy doesn't have CPython's iter/next + StopIteration pattern |
| `cmp_to_key` | Blocked | Pure TPy; blocked on readonly-through-generics. See BUGS.md |
| `total_ordering` | Done | Class macro in `lib/tpy/_functools_macros.py`, re-exported via `lib/tpy/functools.py`. Synthesizes the missing comparison ops from any one of `__lt__` / `__le__` / `__gt__` / `__ge__` plus `__eq__`. Defers synthesis via `ClassInfo.defer_until_macros_complete` so it composes with `@dataclass` regardless of decorator order -- dataclass adds `__eq__` (and optionally `__lt__`/`__le__`/`__gt__`/`__ge__`), total_ordering then picks an anchor and fills in the rest |
| `wraps`, `update_wrapper` | Blocked | CPython's `@wraps(f)` is a decorator factory (`wraps(f)` returns a decorator that takes the wrapper). TPy macro_api has no "decorator factory that's identity" form; would need new infrastructure separate from class/call/builder macros |
| `partial` | Missing | Full variadic form needs function-macro or `*args` forwarding on user classes |
| `partialmethod` | Missing | Descriptor-protocol heavy |
| `lru_cache`, `cache` | Missing | Decorator must wrap + return a new callable with mutable cache dict. `@function_macro` exists now but only rewrites a body in place; it cannot yet return a new wrapping callable, which is what this needs |
| `singledispatch` | Missing | Runtime dispatch; use `@overload` instead |
| `cached_property` | Missing | Needs descriptor support |

### random

Current: `lib/tpy/random.py` -- pure-TPy MT19937 engine. `Random` class
holds the 624-word state; module-level `random()` / `seed()` /
`getrandbits()` delegate to a module-level `_inst: Random` singleton
(auto-seeded from OS entropy at module init, like CPython). Byte-
identical to CPython's `random._inst.getrandbits(32)` for any Int32
seed (negatives are mapped to `abs()` to match CPython); verified
against seeds 42, 1, 7, 99, 12345, -42, INT32_MIN; `cases/stdlib/random`
and `cases/stdlib/random_seq` exercise both TPy and CPython phases.

Target under the policy: the Mersenne Twister state machine itself is **pure
TPy** (same as CPython's `_randommodule.c` logic, but in .py). The only native
primitive is an OS entropy source for seeding when no explicit seed is given
(e.g. `os.urandom` via thin syscall binding). Everything else -- `randint`,
`choice`, `shuffle`, `sample`, `gauss`, etc. -- is pure TPy over the MT core.

**Unblocked (engine landed).** Earlier drafts flagged the MT port as gated
on the `native_module`-facade bugs ("Variable re-exports through native_module
facades" / "Init chain doesn't propagate transitively through native_module
facades" in BUGS.md) and "module-level mutable state across compilation
units." Verified those don't apply here:
`random.py` is a regular stdlib module, not a facade, so it compiles to one
TU with a module-level `_inst: Random` whose state is genuinely shared
across all importers. The one real limitation -- TPy rejects reassigning a
module-level reference-type variable -- is sidestepped by in-place
`_inst._seed(s)` mutation, which is how CPython's `random.seed()` works
anyway.

**Thread safety (deferred).** The module-level `_inst` is shared mutable
state and NOT thread-safe: concurrent callers can corrupt the 624-word
vector (double-twist, torn index updates). Currently theoretical -- TPy
has no threading primitives -- but will need a fix when `threading` lands.
Three candidate models, each with trade-offs: per-thread `_inst` via
thread-local storage (muddies `seed()` semantics), lock inside `Random`
(contention under heavy use), or deprecate module-level helpers in favour
of explicit `Random()` instances (breaks CPython shorthand). CPython itself
punts to "use per-thread Random()" in docs and added internal locking in
free-threaded 3.13+. Decision deferred until the TPy threading model is
chosen.

Sketch:

    class Random:
        _state: Array[UInt32, 624]
        _index: UInt32

        def __init__(self, seed_value: UInt32 | None = None) -> None: ...
        def _seed(self, s: UInt32) -> None: ...          # init_by_array
        def _genrand_uint32(self) -> UInt32: ...         # MT step + twist
        def random(self) -> float: ...                   # genrand_res53
        def choice[T](self, seq: list[T]) -> T: ...
        def shuffle[T](self, seq: list[T]) -> None: ...
        # randint, gauss, ... as methods

    _inst: Random = Random()  # auto-seeds via _os_entropy_uint32()

    @overload
    def seed() -> None: _inst._seed(_os_entropy_uint32())
    @overload
    def seed(n: Int32) -> None: ...                       # negatives -> abs()
    # ...

OS entropy primitive shipped as `tpy::stdlib::random::os_entropy_uint32`
in `runtime/cpp/include/tpy/stdlib/random.hpp`, backed by
`std::random_device`. Used for the no-arg `seed()` and `Random(None)`
auto-seed paths. `SystemRandom` reuses the same primitive but is
deferred pending class-hierarchy decisions (subclass `Random` with
overrides vs standalone class).

Soft gaps for full CPython compat (none gate the core MT port):
- `choices` / `sample` kwarg iterables (`weights=`, `cum_weights=`, `counts=`)
  ship as `list[float]`/`list[int]` rather than `Iterable[T]`. The list-
  literal-vs-protocol conformance gap that blocked this has been closed
  (see functools/math stdlib updates); remaining work is a mechanical
  signature swap plus mixed-positional+kwarg argument wiring.

| Item | Status | Notes |
|---|---|---|
| `random()` | Done | Uniform [0, 1); MT19937 `genrand_res53`. Byte-identical to CPython |
| `seed(a)` | Partial | Int32 (negatives mapped to abs()) and `seed()` no-arg auto-seed via OS entropy. CPython also accepts BigInt / `bytes` / `str` (Tier 3 below) |
| `getrandbits(k)` | Done | Returns `int` (BigInt). k in [1, 32] uses one MT word; k > 32 concatenates ceil(k/32) words little-endian, matching CPython byte-identical |
| `randint(a, b)` | Done | Inclusive [a, b]; `b - a + 1` must fit Int32 |
| `randrange(stop)`, `randrange(start, stop)`, `randrange(start, stop, step)` | Done | Three overloads; step can be negative |
| `randbytes(n)` | Done | Byte-identical to CPython's `getrandbits(n*8).to_bytes(n, 'little')` for any `n` on any host |
| `choice(seq)` | Done | Pure-TPy generic `choice[T](seq: list[T]) -> T`. Byte-identical to CPython on the same seed |
| `shuffle(seq)` | Done | Fisher-Yates / Durstenfeld in place; byte-identical to CPython |
| `uniform(a, b)` | Done | |
| `triangular(low=0.0, high=1.0, mode=None)` | Done | |
| `gauss(mu, sigma)` | Done | Box-Muller with cached second value. Reseed clears cache |
| `normalvariate(mu, sigma)` | Done | Kinderman-Monahan (distinct stream from `gauss`), matches CPython |
| `lognormvariate(mu, sigma)` | Done | |
| `expovariate(lambd)` | Done | |
| `paretovariate(alpha)` | Done | |
| `weibullvariate(alpha, beta)` | Done | |
| `gammavariate(alpha, beta)` | Done | Cheng 1977 (alpha>1) + Ahrens-Dieter (0<alpha<1) + exponential (alpha==1) |
| `betavariate(alpha, beta)` | Done | Composed over `gammavariate` |
| `vonmisesvariate(mu, kappa)` | Done | Floor-mod workaround for BUGS.md "tpy::fmod uses C semantics" (`%` sign semantics) |
| `Random` class (per-instance state) | Done | Per-instance 624-word state; `Random(None)` / `Random()` auto-seed from OS entropy |
| `getstate()`, `setstate(state)` | Missing | Tier 2; CPython tuple shape awkward, `list[UInt32]` variant viable |
| `choices(pop, weights=, cum_weights=, k=)` | Missing | Tier 3. The `Iterable[T]` conformance gap that previously blocked this is resolved; remaining work is a mechanical signature swap plus weighted-selection wiring |
| `sample(pop, k, counts=None)` | Missing | Tier 3: same `Iterable[T]` gap + complex algorithm |
| `binomialvariate(n, p)` | Missing | Tier 3: BTRS state machine; defer until demand |
| `SystemRandom` class | Missing | Tier 3: OS entropy primitive is wired (`tpy::stdlib::random::os_entropy_uint32`); needs class-hierarchy decisions (subclass `Random` with overrides vs standalone) |

Tests: `cases/builtins/random_basic` (existing API smoke test);
`cases/stdlib/random` (MT engine byte-identity with CPython + per-instance
Random + singleton isolation, `cpy` phase enabled);
`cases/stdlib/random_seq` (choice/shuffle/getrandbits k>32/seed(negative)
byte-identity); `cases/stdlib/random_autoseed` (entropy-driven `seed()` /
`Random(None)`, `no_cpython` since entropy is non-deterministic).

### struct

Current: `lib/tpy/struct.py` -- macro module. Format string must be literal.

| Item | Status | Notes |
|---|---|---|
| `unpack(fmt, data)`, `unpack_from` | Done | Little-endian only; big-endian emits MacroError |
| `calcsize(fmt)` | Done | Compile-time constant |
| `pack(fmt, *values)`, `pack_into` | Missing | Needs statement-expr or buffer-builder pattern (see module docstring) |
| `iter_unpack` | Missing | |
| `Struct` class | Missing | Would need per-class macro |
| Big-endian byte order (`>`, `!`) | Missing | Need `std::byteswap` wrappers in unsafe |
| Format codes `e` (f16), `P` (ptr), `n`/`N` (ssize_t/size_t) | Missing | |

Tests: `struct_unpack`.

### bisect

**Done.** `lib/tpy/bisect.py` is pure TPy generic over `Comparable`.

| Item | Status | Notes |
|---|---|---|
| `bisect_left`, `bisect_right`, `insort_left`, `insort_right` | Done | |
| `bisect`, `insort` | Done | Aliases to `bisect_right` / `insort_right` |

Tests: `cases/stdlib/bisect`.

### enum

Current: `lib/tpy/enum.py` -- macro module.

| Item | Status | Notes |
|---|---|---|
| `Enum` base class | Done | |
| `IntEnum` | Done | |
| `auto()` | Done | |
| `StrEnum` | Missing | Python 3.11+ |
| `Flag`, `IntFlag` | Missing | Bitwise semantics |
| Member iteration (`for m in E`) | Partial | Works at runtime; check exhaustiveness |
| Lookup by value (`E(1)`) | Missing | |
| Lookup by name (`E["FOO"]`) | Missing | |
| `.name`, `.value` attributes | Done | |
| Functional API (`E = Enum("E", "A B C")`) | Missing | Rarely used |
| `@unique`, `@verify` decorators | Missing | |

Tests: integrated in json_model and enum test group.

### dataclasses

Current: `lib/tpy/dataclasses.py` -- macro module.

| Item | Status | Notes |
|---|---|---|
| `@dataclass(frozen, order)` | Done | |
| `field(default, default_factory)` | Done | |
| `asdict()`, `astuple()` | Done | Recurse into nested dataclasses, lists, dicts, tuples |
| `@dataclass(slots)` | N/A | All TPy records use inline storage |
| `@dataclass(eq=False, repr=False, init=False)` | Missing | Opt-outs |
| `@dataclass(kw_only)` | Missing | |
| `__post_init__` | Done | Called once at end of synthesized `__init__`; no InitVar args yet. Double-call edge when a child overrides a parent hook (BUGS.md) |
| `InitVar[T]` | Missing | Blocks passing init-only args to `__post_init__` |
| `replace(obj, **kw)` | Missing | Would be a call macro |
| `fields(cls)`, `is_dataclass` | Missing | Needs compile-time or runtime reflection |
| `field(metadata=...)` | Missing | Currently ignored |
| `MISSING` sentinel | Missing | |

Tests: multiple `tplib/json_model_*` cases exercise @dataclass.

### typing

Current: `lib/tpy/typing.py` -- re-export from `tpy._typing`.

| Item | Status | Notes |
|---|---|---|
| `Protocol`, `runtime_checkable` | Partial | Protocols via `Protocol`; `@runtime_checkable` N/A |
| `Self` | Done | |
| `overload`, `override` | Done | |
| `Sized`, `Iterable`, `Iterator`, `Sequence`, `MutableSequence` | Done | |
| `Optional`, `Final` | Done | |
| `Callable` | Done | |
| `Literal` | Done | |
| `TypedDict`, `Unpack` | Done | |
| `Union`, `Annotated` | Missing | TPy uses `A \| B` syntax |
| `Any` | Missing | Type-system gap; would need dynamic dispatch |
| `TypeVar`, `Generic`, `ParamSpec`, `TypeVarTuple` | Missing | TPy uses PEP 695 `[T]` syntax |
| `ClassVar` | Missing | |
| `NewType` | Missing | Could be macro |
| `cast` | Missing | Open question: explicit upcast syntax |
| `get_type_hints`, `get_origin`, `get_args` | Missing | Runtime reflection |

### datetime

**Partial (v1 + v2 + v3 landed).** See `docs/DATETIME_DESIGN.md` for the full
design and phased roadmap (v0 `@overload`-operator codegen fix -> v1
`timedelta`+`date` -> v2 naive `datetime`+`time` with wall clock -> v3
formatting/parsing + fixed-offset `timezone` awareness).
Value-typed frozen dataclasses; pure-TPy calendar math, formatting, and
parsing. The vendored Hinnant `date` tz backend (`--date=bundled|system|
auto|none`) sits behind the `tpy/stdlib/datetime.hpp` facade -- the module's
only OS dependencies are `time.time_ns()`, `local_utc_offset_seconds(epoch)`
and `local_zone_abbrev(epoch)`. The backend honors `TZ` (IANA names via the
tz database, POSIX rule strings via the provider's POSIX reader, empty or
unparseable -> UTC, unset -> `/etc/localtime`), resolved ONCE at first use
and pinned for process life -- a mid-run `TZ` change (libc rereads;
`time.tzset()` is a TPy no-op) is the residual documented divergence.

| Item | Status |
|---|---|
| `timedelta`, `date` | Done (v1, integer surface -- byte-parity with CPython) |
| `time`, `datetime` | Done (v2, hand-written `datetime` ordering, `@overload` `dt - dt` / `dt - td`) |
| `datetime.now/utcnow/today/fromtimestamp/utcfromtimestamp/combine`, `date.today` | Done (v2; v3 added the `tz` params to now/fromtimestamp/combine; out-of-range timestamp raises `ValueError`, beyond-time_t raises `OverflowError`) |
| fixed-offset `timezone`, aware `datetime` (utcoffset/tzname/dst, aware arithmetic/comparison/hash, `astimezone`) | Done (v3; awareness is a runtime property, naive/aware ordering+subtraction raise `TypeError` like CPython; `timezone.utc` is spelled via the module-level `UTC` alias -- class attr is a loud compile error, see DATETIME_DESIGN divergences) |
| `strftime` (`date`/`time`/`datetime`) | Done (v3; full documented directive set incl. `%c/%x/%X` C-locale compositions and ISO `%G/%V/%u`; hardcoded English tables, `LC_TIME` never consulted) |
| `datetime.strptime` | Done (v3; `_strptime.py`-equivalent acceptance rules; `%Z` accepts UTC/GMT only -- stricter than CPython's host-dependent set) |
| `fromisoformat` (`date`/`time`/`datetime`) | Done (v3, 3.11+ grammar; `time.fromisoformat` rejects an offset suffix -- aware `time` deferred) |
| `isoformat` | Done (`sep` + `timespec` params; aware values append the offset) |
| `datetime.timestamp()`, `replace()` | Done (v3; naive timestamp/astimezone use the CPython `_mktime` iterative local-inverse solve, exact across DST gaps/folds; `replace` uses CPython's own `tzinfo=True` sentinel signature) |
| `dt.date()`, `dt.time()` accessors | Done -- `time()` drops tzinfo (naive result) like CPython (test `stdlib/datetime_accessors`) |
| `ZoneInfo` (IANA zones), `zoneinfo` module, PEP 495 `fold` | Done (v4; tz slot is the closed value union `timezone \| ZoneInfo \| None`; ZoneInfo is equal-by-key -- CPython's per-key cache behavior; both fold values byte-verified at a real gap and fold window incl. timestamp/fromtimestamp/astimezone auto-fold) |
| `zoneinfo.available_timezones()`, `ZoneInfo.fromutc` | Done (v4.1; the set is the provider's zone database, so every listed key constructs -- CPython's placeholder entries `Factory`/`localtime` are excluded, a declared divergence; `fromutc` sets PEP 495 fold on the second pass). Also declared: `ZoneInfo("posix/...")`/`ZoneInfo("right/...")` raise `ZoneInfoNotFoundError` even where CPython's raw file lookup constructs those legacy-tree keys (the provider's db walk skips them; use the plain zone name) |
| `ZoneInfo.no_cache` / `clear_cache` | **Permanently unsupported** (loud absence): their whole observable effect is identity-distinct same-key instances (inexpressible in a value type -- this is exactly where CPython's identity-equality diverges from equal-by-key) and mid-run tz-db reload (conflicts with the pin-once provider; the tzset-re-resolve TODO item is the sanctioned reload path) |
| `ZoneInfo.from_file`, `TZPATH` / `reset_tzpath` / `PYTHONTZPATH` | Deferred (backend work: no public TZif-stream parser in the vendored provider; custom search paths need provider support) |
| aware `time` (+ `time.fold`), user `tzinfo` subclasses | Deferred (aware `time` incl. its inert `fold`; user subclasses permanently unsupported) |
| `timedelta` arithmetic | Done (integer surface + float operators: `timedelta / number`, `td */` float, round-half-to-even); float constructor args deferred (rejected) |

### csv

**Partial.** `lib/tpy/csv.py` -- pure TPy over the io `Readable`/`Writable`
protocols. `reader` is a generator that borrows fp; `writer`, `DictReader`, and
`DictWriter` are holders storing a borrowed `Ptr` to the concrete file type
(file objects are `@nocopy`, so they borrow rather than own -- matching CPython,
where the csv objects don't own the file). CPython resolves to the real `csv`
module under the cpy phase, so the surface is byte-compared directly. The row
parser exists twice (`reader` over a `Readable` protocol param for cross-module
rvalue calls; `_parse_rows` over a `Ptr[R]` for `DictReader`) -- they can't
share under current constraints (see TODO.md).

| Item | Status | Notes |
|---|---|---|
| `reader(fp, *, delimiter, quotechar, doublequote, skipinitialspace)` | Done | Char state machine: quoted fields, embedded delimiters, embedded newlines (continuation lines), doubled-quote unescape, `\r\n`/`\n`. Yields `list[str]` per row. |
| `writer(fp, *, delimiter, quotechar, doublequote, lineterminator)` | Done | `writerow` / `writerows`. QUOTE_MINIMAL: a field is quoted only if it contains the delimiter, quotechar, CR, or LF. Default lineterminator `\r\n`. |
| `DictReader(fp, fieldnames=None, *, dialect kwargs)` | Done | Yields `dict[str, str]`; the first row supplies `fieldnames` when none given. Import directly (`from csv import DictReader`) -- module-qualified generic-class construction is a compiler gap. Short rows pad missing fields with `""` (CPython `restval=None`); long rows drop trailing extras (CPython collects them under `restkey`) -- both forced by `dict[str, str]` not holding None or a list. |
| `DictWriter(fp, fieldnames, *, restval="", dialect kwargs)` | Done | `writeheader` / `writerow(dict)` / `writerows`. Matches CPython defaults: a missing field is written as `restval` (default ""); a key not in fieldnames raises `ValueError` (extrasaction='raise'). `extrasaction='ignore'` not yet configurable. |
| `escapechar`, quoting constants (`QUOTE_ALL`/`QUOTE_NONNUMERIC`/`QUOTE_NONE`) | Missing | v1 is QUOTE_MINIMAL only. |
| `Dialect` objects, `register_dialect`, `Sniffer` | Missing | -- |

Tests: `cases/stdlib/csv_reader_writer` (reader quoting/edge cases + writer
QUOTE_MINIMAL + round-trip, cpy parity); `cases/stdlib/csv_dictreader_writer`
(DictReader header-derived + explicit fieldnames, DictWriter, quoted fields,
short-row padding, round-trip, cpy parity).

### base64

Current: `lib/tpy/base64.py` -- pure TPy. CPython-compatible defaults:
`b64decode(validate=False)` silently skips non-alphabet chars (matching
CPython's lax MIME-mode behavior); `validate=True` makes any non-alphabet
char raise `ValueError`. Padding errors always raise.

| Item | Status | Notes |
|---|---|---|
| `b64encode(data, altchars=None)` | Done | Standard `+/` alphabet; `altchars` builds a custom-alphabet view |
| `b64decode(data, altchars=None, validate=False)` | Done | `validate=False` default matches CPython |
| `standard_b64encode`, `standard_b64decode` | Done | Aliases over the standard alphabet |
| `urlsafe_b64encode`, `urlsafe_b64decode` | Done | `-_` alphabet |
| `b16encode`, `b16decode(data, casefold=False)` | Done | Hex; uppercase output; `casefold=True` accepts lowercase on decode |
| `b32encode(data)` | Done | RFC 4648 base32; all five padding remainders covered |
| `b32decode(data, casefold=False, map01=None)` | Done | Strict by default; `casefold=True` accepts lowercase; `map01` maps `'0'`->`'O'` and `'1'`->`'I'` or `'L'` |
| `encodebytes(data)` / `decodebytes(data)` | Done | MIME-style 76-char line wrap with trailing `\n`; decode passes through `b64decode` (lax) |
| `bytes` / `bytearray` inputs | Done | Auto-converted via `__span__` (generated signature is `std::span<const uint8_t>`) |
| `str` input on decoders (`b64decode`, `standard_b64decode`, `urlsafe_b64decode`, `b32decode`, `b16decode`) | Done | `@overload` delegating through `.encode()`; matches CPython which accepts ASCII str on decoders |
| `BytesView` input | Partial | Works as C++ span but TPy-level coercion not yet tested |
| `b85encode`/`b85decode`, `a85encode`/`a85decode` | Missing | Rare; separate ~100-LOC algorithms |
| `memoryview` input | Blocked | Depends on `memoryview` builtin (see `builtins` section) |

Tests: `cases/stdlib/base64`.

### hashlib

Current: `lib/tpy/hashlib.py` -- pure-TPy FIPS 180-4 port. Only SHA-256
shipped so far; MD5/SHA-1/SHA-512 are mechanical follow-ups using the
same class pattern (new H0/K constants, swap the round function,
little-endian for MD5). Needed new shared primitives to get here:

- `add_wrap`/`sub_wrap`/`mul_wrap` on all eight fixed-width int types
  (Int8/16/32/64, UInt8/16/32/64) -- wrapping modular arithmetic,
  analogous to Rust's `wrapping_add`. Signed variants route through the
  unsigned type (`static_cast<intN_t>(static_cast<uintN_t>(a) OP ...)`)
  to get defined wrap without signed-overflow UB.
- `tpy.bits` module (`rotl32`/`rotr32`/`rotl64`/`rotr64`/`byteswap32`/
  `byteswap64`) wrapping C++20 `std::rotl`/`std::rotr` and C++23
  `std::byteswap`. Re-usable for ciphers, RNGs, `struct`, and
  struct-of-ints packing beyond hashlib.

| Item | Status | Notes |
|---|---|---|
| `sha256(data=None)` + `SHA256` class | Done | FIPS 180-4 algorithm, verified against NIST vectors + CPython |
| `.update(data)`, `.digest()`, `.hexdigest()`, `.copy()` | Done | On `SHA256` |
| `.digest_size`, `.block_size`, `.name` | Done | Instance attrs |
| `md5`, `MD5` | Missing | Next slice; same pattern with little-endian state |
| `sha1`, `SHA1` | Missing | Same pattern, 20-byte digest |
| `sha512`, `SHA512` | Missing | Same pattern, 64-bit words -- needs UInt64 rotate + add_wrap (both already present) |
| `blake2b`, `blake2s` | Missing | Pure TPy; Python has its own impl too |
| `sha3_*`, `shake_*` | Missing | Later |
| `new(name)` dispatcher | Missing | Returns a hash object by name; needs `type(x)`-style dispatch or a dict-of-factories |
| `algorithms_available` / `algorithms_guaranteed` | Missing | Module-level `set[str]` / `frozenset[str]` |
| Optional OpenSSL backend | Missing | Future; gated by F8 feature flag. Only justified by a perf-critical use case |

Design note: `hashlib.sha256(data=None)` takes `bytes | None = None`
instead of CPython's `b""` default because TPy sema rejects non-literal
constant defaults (see BUGS.md "Default parameter value `b\"\"` rejected").
Callers pass either nothing or bytes;
behavior matches CPython.

Tests: `cases/stdlib/hashlib`.

### argparse

Current: `lib/tpy/argparse.py` -- builder-trace macro (Phase 7 of the
macro system; see `docs/MACRO_DESIGN.md`). The compiler walks the
``ArgumentParser`` builder calls at compile time and synthesizes a
per-call-site record + parse function, so ``args`` is statically typed.

| Item | Status | Notes |
|---|---|---|
| Positional arguments | Done | Default str type |
| Optional flags (`-x` / `--foo`) | Done | One or more aliases per add_argument |
| `type=int\|float\|str` | Done | int maps to BigInt to match CPython |
| `default=<literal>` | Done | Scalar literals plus list literals for list-typed actions (append/extend or store + nargs=*/+/<int>) |
| `const=<literal>` | Done | For `store_const` and `store + nargs='?'` |
| `action=` | Done | `store` / `store_true` / `store_false` / `count` / `append` / `extend` / `store_const` |
| `nargs=` | Done | `'?'` / `'*'` / `'+'` / positive int. Variable nargs positionals must be last |
| `choices=(...)` | Done | Macro-time literal sequence |
| `required=True` | Done | Optional flags only |
| `dest=` | Done | Override synthesized record field name |
| `help=` (data) | Done | Stored at macro time |
| `metavar=` | Done | Per-arg display name override for usage / help |
| Optional[T] field for absent flag | Done | When no `default=` and not `required=` |
| ArgumentParser `description=` | Done | |
| `--help` / `-h` auto-generation | Done | Pre-rendered help printer + argv prelude that exits via `sys.exit(0)` |
| `add_help=False` opt-out | Done | Suppresses both the auto printer and the `-h` / `--help` reservation, so users can register their own |
| `prog=` / `usage=` / `epilog=` | Done | Help-text customization. `prog=` substitutes through usage and the `<prog>: error:` parse-error prefix; `usage=` overrides the auto-generated tail; `epilog=` appends after the options block |
| **TODO**: runtime-derived `prog` default | Missing | CPython uses `os.path.basename(sys.argv[0])` when `prog=` is omitted; we hardcode `"prog"`. Closing this needs a `basename` helper in the TPy stdlib + switching the help printer from a pre-rendered literal to a runtime template. Tracked in MACRO_DESIGN.md's argparse Future Work |
| **TODO**: terminal-width-aware help wrap | Missing | Help wraps at a hardcoded 80 cols; CPython argparse uses `shutil.get_terminal_size().columns` at runtime. The cpy-phase test pins `COLUMNS=80` so the comparison is deterministic, but a user running our binary in a 200-col terminal still sees help wrapped at 80 while CPython would wrap at 200. Pairs with the runtime-derived `prog` refactor -- both need the help printer to become a runtime template instead of a pre-rendered literal |
| `type=Int32 / Int64 / UInt8 / ...` | Done | All eight fixed-width ints accepted; field carries the matching primitive |
| `type=Float32` | Done | Field carries `Float32` (32-bit), parse uses `tpy::float32_from_str` (no Float64 round-trip). Default literals are wrapped in `Float32(...)` so the field type matches |
| Subparsers | Done | `add_subparsers()` returns a sub-builder via `@builder_returns`; each `add_parser(name)` returns a sub-builder collecting its own arg specs. Top namespace lays per-sub fields out flat (`Optional[T]` per name, mirroring CPython argparse's Namespace shape) so portable test code reads `args.cmd` / `args.<sub-field>` under both backends. Honored kwargs: `dest=` (top field for the chosen subcommand name; default `"cmd"`), `required=`, `help=` (per-sub help text, rendered in --help). Macro-time errors: double `add_subparsers()`, top parser with positional args + subparsers (regex matcher gap), nested `add_subparsers()` in a sub-parser, sub-parser dest collision with a common arg or with `sp.dest`, same per-sub field name with conflicting types across subs. Limitations: typed-union escape hatch (``args._subcommand: A | B`` for `match`/`case`) intentionally not stored -- synth records carry the `__tpy_builder_` private prefix that user code can't reference, so the union would be unreachable for `match`/`case`. The pre-pass-6 builder-trace move (now landed) lifted the sema phasing wall that previously blocked emitting `@property` forwarders over the union; closing the rest needs reachability for the synth records plus per-sub forwarder emission alongside the flat fields. CPython divergence: TPy preemptively populates every per-sub field as `None` on the top namespace; CPython only sets attributes for the chosen sub. Tests using non-active per-sub fields therefore need `getattr(args, ..., None)` (or skip cpy phase) |
| `add_mutually_exclusive_group()` | Missing | At-most-one constraint across flags |
| Custom `type=<T>` via `from_arg` | Done | Duck-typed: any record with `@staticmethod from_arg(s: str) -> Self` can be passed as `type=`. Macro emits `T.from_arg(token)`; string defaults route through `from_arg` (mirrors CPython's "string defaults run through type="). All four nargs shapes plus `action=store/append/extend` work; required positional / required-flag fields land as plain `T` (not `Optional[T]`) via an accumulator + post-loop unwrap. Remaining gaps: `choices=` would have to compare unparsed tokens (CPython compares parsed values), and list defaults (`default=["a","b"]`) diverge from CPython too (CPython leaves list-default elements as raw strings) -- both stay rejected |
| `parents=`, argument groups, `BooleanOptionalAction`, `allow_abbrev`, `fromfile_prefix_chars`, custom formatter classes, `action=<callable>` | Future | Tier-3; full tier table in MACRO_DESIGN.md's argparse Future Work section |
| Parse-time error wording still differs from CPython | v1 divergence | Stderr+`sys.exit(2)` shape matches; runtime-derived `prog` (`os.path.basename(sys.argv[0])`) and message phrasing parity (e.g. "the following arguments are required") are Tier 2 |

Tests: `cases/argparse/{basic,optional_flags,value_free_actions,list_and_const_actions,choices_required_dest,nargs,empty_parser,positional_nargs_optional,qualified_import,two_parsers_same_module,optional_list_absent,no_argv_uses_sys_argv,fixed_width_types,float32_type,custom_type,custom_type_list,custom_type_positional_nargs,help_basic,help_usage_wrap,explicit_sys_import,prog_epilog,usage_override,metavar,add_help_false,list_default,subparsers_basic,subparsers_common_arg,subparsers_optional,subparsers_help,subparsers_sub_positional,subparsers_sub_required_flag,subparsers_required_unify}` plus `error_argparse_*` cases pinning macro-time validation, `error_subparsers_*` cases (`double_call`, `top_positional`, `nested`, `dest_collide`, `no_subs`, `per_sub_collide_common`, `per_sub_type_conflict`) for subparser-specific validation, and `panic_argparse_*` / `panic_subparsers_*` cases for parse-error stderr+`sys.exit(2)` paths. Help-output cases that pass `prog=` explicitly (`prog_epilog`, `usage_override`, `help_usage_wrap`) run under both backends and assert byte-identical output vs CPython's stdlib argparse; the remaining help cases (`help_basic`, `metavar`) carry `no_cpython.txt` because their hardcoded `"prog"` default differs from CPython's `basename(sys.argv[0])`.

### logging

**Missing.** Needs module-level mutable state + handler/formatter architecture.

### configparser

**Missing.** Depends on `io`.

### urllib.parse

Current: `lib/tpy/urllib/parse.py` -- pure TPy (no network dependency).
CPython-faithful algorithms with one deliberate API-shape divergence:
`urlsplit`/`urlparse` return a **record** (`SplitResult` / `ParseResult`) with
named attributes + `.geturl()`/`.hostname`/`.port`/`.username`/`.password`, not
a `namedtuple` -- so integer indexing (`r[0]`) and unpacking (`a, b, ... =
urlsplit(u)`) are unavailable (both are compile errors, never silent). The
netloc accessors raise `ValueError` on a bad port (non-ASCII-digit) and
`urlsplit` raises on an unbalanced IPv6 `[...]`, matching CPython; the
`uses_relative`/`uses_netloc`/`uses_params` scheme tables are CPython's full
lists. One known divergence: `unquote` of an invalid UTF-8 percent-sequence
returns the raw bytes where CPython substitutes U+FFFD (needs a lossy UTF-8
decode the runtime lacks).

| Item | Status | Notes |
|---|---|---|
| `urlsplit` / `urlunsplit` | Done | `urlunsplit` takes a 5-tuple (CPython shape) |
| `urlparse` / `urlunparse` | Done | splits the legacy `;params` segment for params-using schemes |
| `urljoin` | Done | RFC 3986 relative resolution (`.`/`..`, abs/scheme-relative, query/fragment-only) |
| `quote` / `quote_plus` | Done | unreserved set + `safe=`; UTF-8 multibyte -> `%XX` per byte |
| `unquote` / `unquote_plus` | Done | degenerate escapes (`100%`, `%zz`) left literal, per CPython |
| `urlencode(dict[str, str])` | Done | insertion order (TPy dicts are ordered); `quote_plus`-encoded |
| `parse_qsl` | Done | `keep_blank_values` supported |
| `.hostname`/`.port`/`.username`/`.password` | Done | netloc decomposition incl. `[ipv6]` brackets |
| `parse_qs` (dict-of-lists) | Missing | needs `dict[str, list[str]]` build |
| bytes variants (`quote_from_bytes`, `unquote_to_bytes`, `SplitResultBytes`) | Missing | |
| `urldefrag`; `urlencode(doseq=)`; `quote(encoding=)` | Missing | extra params/forms |
| tuple indexing / unpacking of results | Missing | record, not namedtuple (see above) |

Tests: `cases/stdlib/urllib_parse`, `cases/stdlib/urllib_parse_quote`,
`cases/stdlib/urllib_parse_join`, `cases/stdlib/error_urllib_parse_index`.

### heapq

Current: `lib/tpy/heapq.py` -- pure TPy over `list[T: Comparable]`. Mirrors
CPython's algorithm (sift-up/sift-down) line-for-line; heap items ordered by
`<`.

| Item | Status | Notes |
|---|---|---|
| `heappush`, `heappop` | Done | |
| `heapify` | Done | O(n) bottom-up construction |
| `heappushpop` | Done | Push then pop in one step |
| `heapreplace` | Done | Pop then push in one step |
| `nsmallest(n, a)` | Done | Heap-based O(n + k log n). Takes `list[T]` (CPython accepts `Iterable[T]`). Body uses `a.copy()` which requires list semantics; a switch to `Iterable[T]` would need `list(a)` materialization plus a story for the empty-input generic-T case (CPython returns `[]`; TPy would error on T inference). Deferred to when empty-iterable generic inference is resolved |
| `nlargest(n, a)` | Done | Sort-based O(n log n). Size-k-heap variant (O(n log k)) is a perf follow-up. Same `list[T]` vs `Iterable[T]` gap as `nsmallest` |
| `merge(*iterables)` | Done | Lazy, stable n-way merge over `list[T]` inputs via a per-input cursor scan (O(inputs)/elem vs CPython's O(log inputs) heap; inputs compared in place). Returns `Iterator[Own[T]]` -- yields owned copies, so reference-type results collect into a list with no implicit-copy warning, diverging from CPython's identity-preserving aliasing. Gaps: arbitrary iterables (needs dynamic-iterator erasure + protocol varargs), `key=`/`reverse=` (`key=` blocked on the readonly-through-generic-T callable gap; `reverse=` a cheap follow-up), bare `merge()` needs explicit `T` |
| `key=` arg on `nlargest`/`nsmallest` | Missing | Needs `Callable[[T], K: Comparable]` threading; straightforward add once prioritized |

Design note: CPython's `heapq` operates on any mutable sequence; TPy restricts
to `list[T]` for now. Ref-type heaps work because (1) `heappush` / `heappushpop`
/ `heapreplace` take `item: Own[T]` -- caller transfers ownership of the new
element -- and (2) internal `_siftup` / `_siftdown` reads go through `copy()`
(mirroring the `bisect.insort_left` pattern); users get explicit ownership
and copy semantics for reference types. T must be copyable -- `@nocopy`
element types fail at C++ compile time with a deleted-copy-constructor
error today (clean sema diagnostic tracked in `BUGS.md`).

Tests: `cases/stdlib/heapq`, `cases/stdlib/heapq_ref_type`, `cases/stdlib/heapq_merge`, `cases/stdlib/heapq_merge_ref_copy`, `cases/stdlib/error_heapq_merge_no_args`.

### copy

**Missing.** `copy`/`deepcopy` need generic copy intrinsic. TPy has `copy()` builtin
for value types; dataclass deep-copy would need macro-driven recursion similar to asdict.

### textwrap

**Missing.** Pure-TPy candidate.

### decimal

**Missing.** Large surface. Either bind `mpdecimal` or build pure TPy atop BigInt.

### fractions

**Missing.** Pure TPy over BigInt; small surface.

### statistics

**Missing.** Pure TPy (mean/median/mode/stdev/variance).

### pickle

**Blocked.** Needs runtime type info + `io`.

### shelve

**Blocked.** Needs pickle + dbm.

### inspect

**Blocked.** Needs runtime type/function introspection.

### asyncio

**Partial (v1).** Single-threaded executor + minimal viable surface. Design in [`docs/ASYNC_DESIGN.md`](ASYNC_DESIGN.md); status in [`docs/ASYNC_PROGRESS.md`](ASYNC_PROGRESS.md).

Done in v1:

- `asyncio.run(coro)` -- drives a top-level coroutine to completion; sleeps idle on the executor's timer min-heap; cancel-drains remaining spawned tasks at exit so `finally` runs for fire-and-forget tasks.
- `asyncio.sleep(seconds)` -- registers a steady-clock deadline with the running executor; returns `Own[Task[None]]`.
- `asyncio.create_task(coro())` -- registers an async-def call with the running executor and returns `Own[Task[T]]` (T inferred from the async def's return type) sharing state with the executor's task slot.
- `asyncio.Future[T]` -- single-awaiter manual-completion awaitable (`set_result(value)` / `set_exception(exc)` / `done()`); ownership-transfer API on `set_result` so nocopy types flow through. `Future[None]` works (void-payload lowers to `Future<std::monostate>`); `Event` remains the idiomatic no-payload completion signal.
- `asyncio.Event` -- boolean completion signal (`set` / `clear` / `is_set` / `wait`). The no-payload analog of `Future[T]`. CPython parity for the API surface, including `await event.wait()`; TPy also allows awaiting the Event directly (`await event`) as a shorthand. Single-awaiter v1.
- `asyncio.CancelledError` -- raised at the next suspension point of a cancelled task; thread through `try`/`finally`.
- `Executor` body (slot table for parked tasks with `(slot_id, generation)` wakers, runnable deque, timer min-heap keyed on steady-clock deadlines) lives in TPy at `lib/tpy/asyncio/_executor.py`. `Executor` inherits the `@dynamic Awaker` protocol; `Waker.wake()` dispatches through that vtable. `runtime/cpp/include/tpy/async.hpp` carries two pieces: `CancelledError` and the `poll_with_cancel` resume-case helper template (M8) -- no FFI dispatch shell.

v1.5 M3-M6: SHIPPED. Awaits inside arbitrary control flow (`if`/`while`/`for`/`with` sub-bodies + `try`/`except`/`finally` around awaits) via a localized CFG (`tpyc/codegen_cpp/resumable_cfg.py`). `async with` (cleanup-only) and `async for` (with `StopAsyncIteration`) shipped on top of the same machinery. See `docs/ASYNC_PROGRESS.md` for the milestone-by-milestone summary.

v1.5 M8: SHIPPED. `asyncio.wait_for(coro, timeout) -> T` (`async def` free function) + built-in `TimeoutError`. Pumps inner cleanup through `finally`-with-await on deadline; outer cancellation propagates through to the inner coroutine (the auto-emitted resume-case cancel-check now calls `cancel()` on the in-flight sub-coro before polling, so the inner observes `CancelledError` at its suspension point and can run cleanup before the cancellation surfaces). The async-def codegen path was extended in M8 to handle `Own[Awaitable[T]]`-shaped params via a deduced `T_<pname>` extra template arg with the protocol concept constraint, mirroring the existing non-async pattern. Caller-side sub-coro field declarations use `std::remove_cvref_t<decltype(arg)>` to deduce the concrete coro type without sema knowing it.

v1.5 M9: SHIPPED. Two homogeneous entrypoints sharing one `_GatherFuture[T]` engine: `asyncio.gather(*tasks: Task[T]) -> list[T]` (variadic-positional, a sync factory returning the awaitable directly) and `asyncio.gather_list(tasks: list[Task[T]]) -> list[T]` (the list-shaped form). Both run N already-spawned tasks concurrently and collect their results in input order; on the first sub-task failure (or outer cancel) propagate `cancel()` to siblings, drain to settlement, then re-raise the first exception observed. The CPython-shape *heterogeneous* form `gather[*Ts](*coros) -> tuple[*Ts]` remains deferred (blocked on variadic generics + the async-def `*args` codegen gap; see TODO.md / BUGS.md). Implementation: hand-written `_GatherFuture[T]` Rc-clones each task handle into an owned list via a new `Task[T].clone()` method.

Pending (v1.5): CPython-shape *heterogeneous* variadic `gather`, partial / nested try-around-await (see BUGS.md for the specific CFG-build limits).

v1.5 M1: SHIPPED. Sync `with` upgraded to CPython-shape `__exit__(self, exc_type, exc_val, exc_tb) -> bool | None`; `bool` return suppresses, `None` is cleanup-only.

v1.5 M2: SHIPPED. Class-based exception dispatch via `isinstance(exc_val, X)` in `__exit__` (the `Optional[BaseException]` slicing blocker M1 deferred to M2 was resolved by Phase 20). Bare and early-return-narrowed `isinstance` on `BaseException` / `Optional[BaseException]` params both dispatch correctly. See `docs/ASYNC_PROGRESS.md` M2.

v2 I/O reactor M1: SHIPPED. The `Reactor` is the executor's second wake source: `EpollReactor` owns the reactor fd + a single-waiter `fd -> Waker` registry (one-shot arming); `Executor.wait_for_event` blocks in `epoll_wait` / `kevent` bounded by the nearest timer deadline. Low-level surface: `asyncio.get_running_loop().sock_recv(sock, n)` / `sock_sendall(sock, data)` on a non-blocking socket (`socket.socket.setblocking(False)`, an fcntl `O_NONBLOCK` helper), backed by hand-written `_SockRecv` / `_SockSendAll` awaitables. Binding via `lib/tpy/_bindings/posix_epoll.py` + `runtime/cpp/src/stdlib/epoll_impl.cpp`, which holds two backends behind the flat `tpy_epoll_*` ABI: epoll on Linux, kqueue on macOS / *BSD (the system headers confined to the .cpp). See `docs/ASYNC_DESIGN.md` "I/O reactor".

v2 I/O reactor M2: SHIPPED. `asyncio.get_running_loop().sock_accept(sock)` -> `(conn, (host, port))` (conn set non-blocking) and `sock_connect(sock, addr)` (non-blocking connect + `SO_ERROR` check), backed by hand-written `_SockAccept` / `_SockConnect` awaitables. All four `sock_*` methods now drive the public `socket` methods and park on `BlockingIOError` (the errno-keyed `OSError` subclass raised on EAGAIN/EWOULDBLOCK/EINPROGRESS), mirroring CPython's `loop.sock_*`. See `examples/net/async_echo_*`.

v2 streams (client side): SHIPPED. `asyncio.open_connection(host, port)` returns a `(StreamReader, StreamWriter)` that share the connection's `socket` via an `Rc[socket]` cell (either can drive it; it outlives both across awaits). `StreamReader` buffers bytes and fills via `sock_recv` -- `read(n)` (up to n; `n<0` reads to EOF), `readexactly(n)` (raises `IncompleteReadError` carrying the partial bytes), `readline()` (to `\n` or EOF), `readuntil(sep)` (to a `bytes` separator, `IncompleteReadError` on EOF first; no `limit`/`LimitOverrunError`), `at_eof()`. `StreamWriter` buffers `write(data)` (sync) and flushes via `sock_sendall` in `drain()`; `close()` / `wait_closed()`. `IncompleteReadError(EOFError)` added.

v2 streams (server side): SHIPPED. `asyncio.start_server(handler, host, port)` binds a non-blocking listener (SO_REUSEADDR), spawns a free-function `_accept_loop` as a background task, and returns a `@nocopy Server`. The accept loop runs `handler` (an `async def handler(reader, writer)`, passed directly -- the async-fn->Callable coercion) as a task per connection with a fresh `Rc[socket]`-shared `(StreamReader, StreamWriter)` pair. `Server`: `serve_forever()` (serves until cancelled, then re-raises `CancelledError` -- a `close()` elsewhere cancels the accept task it awaits; CPython parity), `close()` (cancels the accept task; in-flight connections keep running), `wait_closed()`, async-with, and `server.sockets[0].getsockname()` for the bound address. Stopped via cancellation rather than catching listener-closed (a try-assigned local isn't yet movable; see BUGS.md). `Server.sockets` is a small `_ServerSockets` proxy (eager `Rc[socket]` clone built in `__init__`, indexable, returns a readonly socket borrow per index) mirroring CPython's `server.sockets[0]` surface for the `getsockname` read. Both lifecycle tests now run under real CPython asyncio. Two documented divergences remain (no test exercises them): `wait_closed()` is a no-op (CPython awaits connection drain -- needs a per-connection task registry on `Server`), and `__aenter__` returns None (blocked on the async-return-of-self codegen gap, BUGS.md); mutating socket methods on `sockets[0]` aren't exposed (readonly borrow).

Pending (v2+): `StreamReader.readuntil` `limit`/`LimitOverrunError` (the method itself shipped); `start_server` parity surface (`Server.sockets`, the `sock=` param to let a test read the port from its own socket); protocol-level swap-in reactor (`@dynamic`-typed executor field + `asyncio.run` factory arg; the kqueue backend already ships at the C-ABI level); additional reactors (io_uring, IOCP on Windows); async generators, `@error_return` async, `__await__` adaptation, multi-thread executor.

v1.1 runtime port: SHIPPED. Executor body + `Task` + `Future` moved from `runtime/cpp/include/tpy/async.hpp` to `lib/tpy/asyncio/_executor.py`, dispatched from C++ via a thread-local `ExecutorOps` function-pointer table.

v1.2: SHIPPED in seven steps. Step 1 added `val_or_ref_t<void>` and ported `asyncio.run` from a C++ template shell to pure TPy. Step 2 added `@cpp_template` auto-move for `Own[T]` args. Step 3 consolidated value-type emission ordering and timer-heap cleanup. Step 4 moved the type-erasure stack (`Task[T]` / `TaskState[T]` / `AnyTask` / `AnyTaskBox`) to TPy via `@dynamic` protocols. Step 5 moved `Poll[T]` itself -- a single `@nocopy class Poll[T]` body covers what the C++ `Poll<T>` + `Poll<void>` + `Poll<T&>` specializations did (storage is `UninitArrayStorage[T, 1] + bool`; void analog rides on `Poll[None]` -> `Poll<std::monostate>`; the reference-T specialization was never instantiated by generated code). Step 6 shrank `async.hpp` by 50 lines (TLS removal + struct/helper moves to TPy) and consolidated `ExecutorHandle` next to `Waker`. Step 7 pivoted dispatch from the C++ `ExecutorOps` function-pointer table to the `@dynamic Awaker` protocol: `Waker` became a pure-TPy `ValueType` holding `Ptr[Awaker]`, `Executor` inherits `Awaker`, and `async.hpp` collapsed to just `CancelledError`. See [`docs/ASYNC_PROGRESS.md`](ASYNC_PROGRESS.md#v1x-milestone-asyncio-runtime-tpy-port-must-precede-v15) for the phase-by-phase history.

### threading

**Not yet ported.** OS threads now exist as the TPy-native `tpy.thread`
(Send+move model, not CPython's GIL-based `threading` -- see
`docs/THREADING_DESIGN.md` "Why not CPython threading"). A faithful CPython
`threading` port would be a thin, deliberately-unsafe convenience layer over
that safe core, not the safety boundary; not prioritized.

### multiprocessing

**Blocked** on process spawning.

### subprocess

**Blocked** on process spawning.

### socket

**Partial.** Pure-TPy facade in `lib/tpy/socket.py` over raw `@native`
bindings in `lib/tpy/_bindings/posix_socket.py`. One out-of-line C++ helper
in `runtime/cpp/src/stdlib/socket_impl.cpp` for DNS resolution
(getaddrinfo walks a `struct addrinfo` whose field order isn't portable
between Linux and BSD/macOS, so the struct walking lives on the C++ side
where `<netdb.h>` is available).

Phase 1 ships a working IPv4 TCP client/server shape sufficient for the
"simple socket client / simple socket server" milestone in the project
roadmap. Phase 2 is non-blocking I/O + `selectors`. Phase 3 is TLS/ssl.
Phase 4 is `http.client` (pure TPy). Phase 5 is websocket client.

Architecture (mirrors the re / PCRE2 split):

  * `runtime/cpp/include/tpy/stdlib/socket_h.hpp` -- ABI-glue header.
    Forward-declares `struct sockaddr_in` (POSIX-stable 16-byte layout)
    with `static_assert` guards on size + field offsets, plus
    `extern "C"` decls of libc socket/bind/connect/... and our three
    runtime helpers. Deliberately does NOT include `<sys/socket.h>` /
    `<netdb.h>` -- those headers `#define` macros (`AF_INET`, `SOCK_STREAM`,
    ...) that would collide with TPy module-level constants of the same
    name.
  * `runtime/cpp/src/stdlib/socket_impl.cpp` -- implements
    `tpy_resolve_ipv4(const uint8_t*, uint64_t, uint8_t[4])`,
    `tpy_errno()`, `tpy_last_resolve_error()`. Includes the system
    headers freely; isolated to its own TU so macros don't escape.
    First inhabitant of `runtime/cpp/src/` (new runtime-lib convention
    -- see "C++ helper convention" above).
  * `lib/tpy/_bindings/posix_socket.py` -- pure `@native` 1:1 bindings over
    the libc C ABI + the three runtime helpers. `@native(binding="C")
    class SockaddrIn` mirrors the struct for typed field access.
  * `lib/tpy/socket.py` -- facade. `socket` class (`@nocopy`, RAII via
    `__del__` closing the fd), SocketError, constants, create_connection
    / create_server, gethostbyname, with-statement support.

| Item | Status | Notes |
|---|---|---|
| `socket(family, type, proto)` | Done | `@nocopy`, RAII close in `__del__`. Class name is lowercase `socket` to match CPython's `socket.socket` exactly. Use `socket(AF_INET, SOCK_STREAM)` or `create_connection` / `create_server` |
| `bind`, `connect`, `listen`, `accept` | Done | `accept() -> tuple[Own[socket], tuple[str, Int32]]` matches CPython's `(conn, (host, port))` shape. `_accept_nonblocking()` (underscore-private, TPy-only) = accept + set the conn non-blocking, used by the asyncio reactor's `sock_accept` |
| `send`, `sendall`, `recv` | Done | `send` returns `Int32` (truncated from `ssize_t`); realistic per-call sends are well under 2 GiB. `recv` returns a fresh `bytes` |
| `close`, `shutdown`, `fileno` | Done | |
| `setsockopt_int`, `getsockopt_int` | Done | Int-valued options only; struct options (`SO_RCVTIMEO`, `SO_LINGER`) deferred. `getsockopt_int` backs the asyncio reactor's `SO_ERROR` check after a non-blocking connect |
| `getsockname`, `getpeername` | Done | Return `tuple[str, Int32]` |
| `gethostbyname` | Done | Resolves via `getaddrinfo` behind `tpy_resolve_ipv4`; returns the first A record only |
| `create_connection`, `create_server` | Done | TCP client/server convenience factories; `create_server` bundles SO_REUSEADDR + bind + listen |
| `with socket(...) as s:` | Done | `__enter__` returns self, `__exit__` closes |
| `SocketError`, `gaierror`, `BlockingIOError`, `ConnectionError` family | Done | `SocketError` wraps errno + strerror, **subclasses `OSError`** (matches CPython), so `except OSError` catches it. `_raise_errno`/`_raise_io` raise `BlockingIOError` (also `OSError`) on EAGAIN/EWOULDBLOCK/EINPROGRESS (the asyncio reactor parks on it) and the PEP 3151 `ConnectionError` subclasses on the connection errno (EPIPE -> `BrokenPipeError`, ECONNRESET -> `ConnectionResetError`, ECONNREFUSED -> `ConnectionRefusedError`, ECONNABORTED -> `ConnectionAbortedError`). All carry the structured `.errno`/`.strerror` OSError attributes (compare against the `errno` module's constants); name-resolution failures raise `gaierror` with the EAI_* code in `.errno` (CPython-shaped). The exception ctors are CPython's `(errno, strerror)` forms and `str(e)` is CPython-exact (`"[Errno N] strerror"`); os-side raises populate `.filename` too. Declared TPy-only behavior: `sendall`'s zero-byte-send guard raises `BrokenPipeError(EPIPE)` (CPython just loops; POSIX `send` doesn't return 0 for a positive-length TCP write, so the branch is practically unreachable) |
| Constants (`AF_INET`, `SOCK_STREAM`, `SOL_SOCKET`, ...) | Done | Platform-stable values are literals; the divergent ones (`SOL_SOCKET`, `SO_*`, `AF_INET6`, `EAGAIN`, `EINPROGRESS`, `EPIPE`, `ECONN*`) read from the system headers via `native_global` -> `tpy_const_*` in `socket_impl.cpp`, so correct on Linux + macOS/BSD |
| `socketpair()` | Done | Defaults to `AF_UNIX` + `SOCK_STREAM`; returns `tuple[Own[socket], Own[socket]]` |
| `setblocking` | Done | `settimeout(None)`/`settimeout(0.0)` equivalents (toggles `O_NONBLOCK` via fcntl); the prerequisite for using a socket with the asyncio epoll reactor (`get_running_loop().sock_recv`/`sock_sendall`) |
| `settimeout`, `gettimeout`, `getblocking` | Done | Timeout mode (`None` blocking / `0.0` non-blocking / `>0` timeout). recv/send via `SO_RCVTIMEO`/`SO_SNDTIMEO` (timeval built in `tpy_set_timeout`, not a `@native` struct); connect via a poll-based wait (`tpy_connect_timeout`); a timeout raises `TimeoutError("timed out")` matching CPython's `socket.timeout`. The `makefile`/`BufferedReader` read path is timeout-aware too. Negative value -> `ValueError`. Not done: `setdefaulttimeout`/`_GLOBAL_DEFAULT_TIMEOUT`, accept-under-timeout. `cases/stdlib/socket_timeout`, `socket_gettimeout`, `socket_connect_timeout` |
| IPv6 / `AF_INET6` | Missing | Needs `SockaddrIn6` binding |
| `AF_UNIX` | Missing | Needs `SockaddrUn` binding |
| `sendto`, `recvfrom`, `recv_into` | Missing | UDP out-addr + recv-into-caller-buffer variants |
| `getaddrinfo` (full API) | Missing | Flat `tpy_resolve_ipv4` only today; multi-result walk needs typed records |
| `makefile(mode="r", buffering=-1)` | Partial | Signature mirrors CPython (default `mode="r"`); v1 implements binary-read modes (`"rb"`/`"br"`/`"b"`) only -> `io.BufferedReader` over a **dup** of the socket fd (reader and socket close independently -- differs from CPython's shared-fd refcount, observably equivalent for request/response reads). Text modes (incl. the bare-`makefile()` default), write modes, and `buffering=0` (unbuffered) raise `ValueError` -- loud, not a silent binary-for-text substitution; they need io's TextIOWrapper/BufferedWriter/raw-SocketIO layers (not built). `cases/stdlib/socket_makefile`, `socket_makefile_unsupported` |
| Windows (Winsock2) | Missing | `SOCKET` unsigned, `WSAStartup`, `closesocket`, `WSAGetLastError` -- all in `#ifdef _WIN32` block inside socket_impl.cpp once we have Windows CI |
| TLS (`ssl` module) | Partial | mbedTLS 3.6.6 vendored (`--mbedtls`). The `ssl` module ships an HTTPS-client core: `create_default_context()`/`SSLContext` (secure-default verify + hostname, `load_verify_locations(cafile)`), `wrap_socket` -> `SSLSocket` (handshake/recv/send/sendall/version/close/`makefile`), `SSLError`/`SSLCertVerificationError`, `CERT_NONE`/`CERT_REQUIRED`. Real TLS 1.3 handshake + cert/hostname verification (socketpair test). `makefile()` returns an `io.BufferedReader` over an `SSLRawIO` (`@dynamic RawBinaryIO`) sharing an `Rc[_SslSession]` (session handle + socket) -- the fd outlives the `SSLSocket` for a live reader, mirroring CPython's refcounted `socket.makefile`. `http.client.HTTPSConnection` runs the HTTP/1.1 flow over an `SSLSocket`, and `tplib.requests` / `urllib.request.urlopen` route `https://` to it (`requests` via `verify: bool|str`, `urlopen` via `context=`; http->https redirects followed). `create_default_context()` trusts a vendored Mozilla root bundle (certifi, embedded as a compiled-in blob via `scripts/vendor_cacert.py`) plus the platform CA bundle (`load_default_certs()`: `SSL_CERT_FILE` override, else well-known bundle paths), so real https verifies out of the box and system-installed corporate CAs work flag-free like curl; `load_verify_locations` is additive. Gaps: macOS Keychain-only CAs, `SSL_CERT_DIR`, Windows (with the port). `SSLWantReadError`/`SSLWantWriteError` are raised by non-blocking `recv`/`send`; the write path maps a `close_notify` return to `SSLZeroReturnError` defensively (`recv` keeps returning `b""` on a clean close, matching CPython's `SSLSocket.recv`; `do_handshake` keeps its bool return -- declared divergence). Server-side TLS ships as a public API: `SSLContext().load_cert_chain(certfile, keyfile)` + `wrap_socket(sock, server_side=True)` runs a real mbedTLS handshake as the server (test `ssl/tls_server`, `no_cpython`); the context is role-agnostic (no `PROTOCOL_TLS_*`/`Purpose`), `server_side=True` requires a loaded cert chain and ignores the client verify/hostname config. REMAINING: mutual-TLS client-cert verification, `PROTOCOL_TLS_*`/`Purpose` constants, `load_cert_chain(password=)`, async-reactor TLS. See `docs/SSL_DESIGN.md` |

Tests:
  * No integration test cases under `tests/cases/` -- running real client/
    server end-to-end needs threading or fork (not in Phase 1), and
    fingerprint-based skip logic doesn't play well with network ports.
  * Examples under `examples/net/` (`tcp_client.py` + `tcp_server.py`)
    serve as manual smoke tests: run the server in one terminal, the
    client in another, verify the echo round-trip.

### http / http.client

**Partial (v1).** Pure-TPy plaintext HTTP/1.1, built on `socket` +
`io.BufferedReader`. `http.HTTPStatus` is a plain `IntEnum` over CPython's
full status-code set (`.value`/`.name`/value-lookup/int-compare; no
`.phrase`/`.description`/`.is_*` -- TPy enums can't carry per-member data, so
those attributes are a compile error, not a silent wrong value).

`http.client.HTTPConnection(host, port=80)` exposes `connect()`,
`request(method, url, body=None, headers=None)`, `getresponse()`, `close()`,
and an assignable `sock` (used for testing). `request()` auto-adds `Host`,
`Accept-Encoding: identity`, and `Content-Length` (incl. `0` for bodyless
POST/PUT/PATCH) in CPython's exact header order. `HTTPResponse` exposes
`status`/`reason`/`version`, `read(amt=-1)`, `getheader(name, default=None)`
(case-insensitive; duplicate values joined with `", "`), and `getheaders()`;
body framing handles Content-Length, chunked transfer-encoding (extensions +
trailers), and connection-close. Reads go through `socket.makefile("rb")` ->
`io.BufferedReader` (the request is written via the socket, the response read
via a dup of the fd). `HTTPException`/`BadStatusLine`/`UnknownProtocol` for
malformed status lines and non-HTTP/1.x versions. Verified byte-parity with
CPython's stdlib `http.client` via socketpair + `conn.sock` injection
(`tests/cases/stdlib/http_status`, `http_client`, `http_client_framing`,
`http_client_bad_status`).

`http.client.HTTPSConnection(host, port=443, timeout=None, context=None)`
runs the same flow over TLS through an `ssl.SSLSocket`. With no `context` it
builds a secure-default `ssl.create_default_context()` (chain + hostname
verification against the vendored Mozilla root bundle, so a real public server
verifies out of the box); a caller-supplied `context` is captured by value. It is a
*sibling* of `HTTPConnection`, not a subclass: TPy method dispatch is static,
so both instead satisfy a `@dynamic _Connection` protocol
(`connect`/`request`/`getresponse`/`close`), letting a caller hold either
behind one `Box[_Connection]` and dispatch virtually. The request-building
logic is shared via module free functions (`_build_request`). Verified over a
real mbedTLS handshake via the `_tls` injection seam against a server-side
`SSLSocket` (`load_cert_chain` + `wrap_socket(server_side=True)`)
(`tests/cases/stdlib/https_client`, `no_cpython`). Because `http.client` now
imports `ssl`, every program importing it links the TLS backend (mbedTLS);
TODO.md tracks the use-driven-linking follow-up to scope that to programs that
actually reference `HTTPSConnection`.

Connections are persistent (HTTP/1.1 keep-alive): the socket survives
request/getresponse cycles and `request()` after `close()` reconnects.
`HTTPResponse.will_close` mirrors CPython's `_check_close` (Connection-header
substring, HTTP/1.0 default-close, unframed-body fallback); the caller drains
each response and closes the connection on `will_close`. Declared divergence:
`getresponse()` does not auto-close on will_close as CPython does (TPy's
`SSLSocket.close()` sends close_notify immediately, which would race a
still-unread TLS body). Chunked bodies accumulate into a `bytearray`
(amortized O(n)). Test: `cases/stdlib/http_client_keepalive` (gold parity).

**Deferred** (see TODO.md): the `http.HTTPMethod` enum, low-level
putrequest/putheader/endheaders, str/file/iterable request bodies,
`email.message`-style `.headers`, `HTTPStatus.phrase`/`.is_*`, header folding,
single-space-vs-whitespace status-line split, proxy/`set_tunnel`, the
`CannotSendRequest`/`ResponseNotReady` misuse guards, pipelining.

### urllib.request

Current: `lib/tpy/urllib/request.py` -- a simplified, signature-compatible
`urlopen(url, data=None, timeout=None, context=None)` over `http.client`
(GET, or POST when `data` is given; `https://` routes to `HTTPSConnection`
with `context=` as the TLS context). Returns an `http.client.HTTPResponse`
(read via `.status`/`.reason`/`.read()`/`.getheader()`). A non-`http(s)`
scheme raises `URLError`. No opener/handler stack, no
redirects/proxies/auth-handlers (see TODO.md). A private `_sock` param injects a socket for offline tests
(the connection is still built from the URL and dropped on return, so the
response's dup-fd reader is exercised as in normal use). Test:
`cases/stdlib/urlopen`.

### tplib.requests

Current: `lib/tpy/tplib/requests.py` -- a `requests`-style client (pure TPy)
over `http.client`. Module fns `get`/`post`/`put`/`patch`/`delete`/`head` +
`request(method, url, ...)` with `params`/`headers`/`data`/`json`/`auth`
kwargs. `Response` exposes `.status_code`/`.reason`/`.url`/`.ok`/`.text`
(UTF-8)/`.content`/`.headers` (`CaseInsensitiveDict`)/`.json()` (untyped
`JsonValue`)/`.raise_for_status()`. `Session` merges default headers/params and
applies a default auth (internal `_connection` field = the offline test seam).
`auth=
(user, pass)` -> Basic via `base64`. Exception tree `RequestException` ->
`HTTPError`/`ConnectionError`/`Timeout`/`TooManyRedirects`; a socket-level
connection failure (refused/reset/broken-pipe/no-host) is re-wrapped into
`ConnectionError` and a timeout into `Timeout`, so neither leaks as a bare
`OSError`. `allow_redirects=` (default True; `head()` defaults False) follows
301/302/303/307/308 via the `Location` header (resolved with `urljoin`);
`Response.history` holds the intermediate responses and `Response.url` is the
final URL; 302/303 coerce any non-HEAD method to GET (301 coerces only POST) and
drop the body, while 307/308 preserve both; a cross-host redirect drops
`Authorization`; exceeding
`Session.max_redirects` (default 30) raises `TooManyRedirects`. **Declarable
divergences** (all compile-visible, none silent): kwargs are a fixed typed set
(no `**kwargs`); `params`/`headers` are `dict[str, str]`; `data` is `bytes`;
`json=` takes a `JsonValue` and an inline dict literal must be bound to a
`JsonValue` local first; `.json()` is untyped (typed path
`Model.from_json(r.text)`); `.text` is UTF-8 only; `.headers` is a
`CaseInsensitiveDict` (case-insensitive lookup, original casing kept for
items/keys, repeated headers joined with `", "`, full mutable-mapping surface
incl. `for k in headers` iteration). `Session` pools connections per
`(scheme, host, port, verify)` (`_pool_key`) and reuses them across requests
(HTTP/1.1 keep-alive); a `will_close` response closes the socket but the
pooled entry stays as a lazy-reconnect handle; a pooled connection keeps the
timeout it was created with. HTTPS: an `https://` URL (or redirect target) routes to
`HTTPSConnection` on port 443; `verify: bool|str` (`True` verified default,
`"<path>"` custom CA, `False` disabled) selects the trust; `ssl.SSLError` wraps
as `requests.SSLError` (a `ConnectionError`); `Authorization` is dropped across a
host/scheme/port change (`should_strip_auth`); a redirect to a non-http/https
scheme (e.g. `ftp`) raises `ConnectionError`. `verify=True` trusts the vendored
Mozilla root bundle, so a public https server verifies with no explicit CA path.
Cookies: a `cookies=` dict is sent on the request (unscoped -- sent to every hop
of that call); a `Session` persists `Set-Cookie` responses in `Session.cookies`
(a `CookieJar`) and resends them domain/path/secure-matched on later requests, so
a cookie set by one host is never leaked to another across a redirect;
`Response.cookies` holds the cookies that response set. Expiry is honored
(`Max-Age`, taking precedence over `Expires`; a past expiry / `Max-Age<=0`
deletes; expired cookies are not sent). Cookie-specific divergences (all
declared): the jar is keyed by name only (a same-name/different-domain collision
is last-wins, not both kept); the default cookie path is `"/"` (not the
request-URI directory); the RFC 1123 and RFC 850 `Expires` forms are parsed
(matching CPython's `http.cookiejar`), the asctime form is not (ignored -- the
cookie is then session-lifetime); `cookies=` accepts a `dict[str, str]` only
(not a whole `CookieJar`).
**Deferred** (see TODO.md): multipart files, streaming, proxies,
form-dict `data=`.
Tests: `cases/tplib/requests_get`, `requests_post`, `requests_session`,
`requests_errors`, `requests_timeout`, `requests_redirect`,
`requests_redirect_disabled`, `requests_redirect_method`,
`requests_redirect_too_many`, `requests_redirect_cross_host`,
`requests_redirect_https`, `requests_headers_ci`, `requests_pool`,
`requests_cookies_send`, `requests_cookies_set`, `requests_cookies_session`,
`requests_cookies_domain`, `requests_cookies_delete`, `requests_cookies_expiry`,
`error_requests_json_inline`.

---

## Adjacent: NumPy / Numeric Computing (parked)

Not CPython stdlib, but tracked here to keep it in sight. Not on the near-term
plan. A separate design doc (`NUMERIC_COMPUTING.md`) will spin off when this
becomes active.

**Key realization**: numpy itself is not wrappable. The Python object layer
(`np.array`, `arr.shape`, methods) and the C API (`PyArray_*`) are hard-coupled
to CPython's refcounting + GIL + PyObject machinery. A natively-compiled TPy
runtime can't host that.

**However, the low-level pieces people assume are "numpy" mostly aren't**:

| Layer | Wrappable from TPy? | What it actually is |
|---|---|---|
| Python object layer | No | Numpy's own, CPython-coupled |
| C API (`PyArray_*`) | No | Numpy's own, CPython-coupled |
| Ufunc dispatch / `NpyIter` broadcasting | Theoretically; not packaged as a standalone lib | Numpy's own |
| Element-wise kernel loops | Theoretically; template-generated, not shipped as reusable | Numpy's own |
| **BLAS / LAPACK / OpenBLAS / MKL** (linear algebra) | **Yes** | Upstream Fortran/C libs; ABI-stable; numpy just wraps them |
| **pocketfft** (FFT) | **Yes** | ~2k LOC C++, MIT, header-only, upstream repo exists; numpy vendors it |
| **sleef** (SIMD transcendentals) | **Yes** | Upstream C lib |

So the "wrap numpy's low-level building blocks" idea collapses into **wrap the
things numpy itself wraps** -- which are CPython-independent upstream libs we
can bind directly via the thin-binding pattern. Gets us the raw compute for
free.

**The container + broadcasting layer is ours to build** regardless. NumPy's
view/refcount model doesn't fit TPy's ownership/borrow model; a
reimplementation is mandatory.

### Candidate backends for the ndarray container

| Option | What it is | License | Fit |
|---|---|---|---|
| **xtensor** | Header-only C++ "numpy for C++, Python-free". `xt::xarray<T>`, broadcasting, lazy expressions, ufuncs, BLAS/LAPACK bindings | BSD-3 | **Strongest candidate** -- explicitly designed for this use case; matches numpy's API style; years of optimization work already done |
| **Eigen** | Header-only C++ linear algebra (matrices, decompositions) | MPL2 | Good for linalg specifically; less numpy-shaped than xtensor |
| **ArrayFire** | GPU-capable numeric library | BSD-3 | Relevant if GPU is ever in scope |
| **blitz++** | Older C++ numeric library with expression templates | Artistic/LGPL | Historical; less active than xtensor |
| **Pure TPy + `Span[T]` / `Array[T, N]`** | Build our own | -- | Maximum control; maximum work. Pressure-tests language features (multi-dim arrays, broadcasting via operator overloads, SIMD intrinsics, bounds-check elision) |

Most likely path: prototype **xtensor-backed** and **pure-TPy** options side-
by-side for the same small API subset (1D/2D dense, element-wise, reductions,
slicing), benchmark, pick.

### Strategy sketch

- **Phase 0 (now)**: parked. Keep this section up to date as thinking
  evolves; don't build.
- **Phase 1**: `tplib.ndarray` -- numpy-*style*, not numpy-compat. 1D/2D
  dense arrays, element-wise ops, reductions, basic slicing. Backend: TBD
  (xtensor vs own). Bind BLAS for linalg separately.
- **Phase 2**: N-D, broadcasting, fancy indexing, dtype system, FFT
  (pocketfft binding).
- **Phase 3 (maybe never)**: a `numpy`-named CPython-compat shim over
  `tplib.ndarray`. Document gaps honestly. Only justified if the shim gets
  close enough to real numpy that users can flip a switch.

### Other angles worth remembering

- **At compile time, macros run under CPython** -- so a macro like
  `@np_table` can freely use real numpy to compute lookup tables, validate
  shapes, or generate specialized kernels that get embedded in the binary.
  Narrow but real use case, and it's available today without any runtime
  numpy support.
- **Nobody has pulled off full numpy compat in a compiled Python.** Codon
  ships a partial numpy-like lib under its own namespace. PyPy's
  `micronumpy` is partial and most users fall back to CPython numpy via
  `cpyext`. Cython/Nuitka/mypyc don't reimplement -- they use real numpy
  because they run on CPython. The honest precedent is "numpy-style
  subset", not "numpy-compat".
