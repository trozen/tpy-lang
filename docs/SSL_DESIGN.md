# SSL / HTTPS-client design

Status: **in progress.** Increments 0-1 (probes, mbedTLS vendoring + build
wiring) and Increment 2 (the `ssl` module core: SSLContext / SSLSocket /
verifying client + handshake test) merged to master. Increment 3a is built:
`SSLSocket.makefile()` over the BufferedReader `Box[RawBinaryIO]` refactor +
an `Rc[_SslSession]` (session handle + socket folded together) shared with
an `SSLRawIO` reader (test `ssl/tls_makefile`). Increment 4 is built:
`http.client.HTTPSConnection` runs the HTTP/1.1 flow over an `SSLSocket` (test
`stdlib/https_client`). Increment 5 is built: `HTTPConnection`/`HTTPSConnection`
now **nominally inherit** the `@dynamic _Connection` protocol (a prerequisite
codegen fix -- @dynamic-override reference-param const-ness -- landed first on
master), and `tplib.requests` (`verify: bool|str`) + `urllib.request.urlopen`
(`context=`) route `https://` through `Box[_Connection]`, including http->https
redirects (tests `tplib/requests_https`, `tplib/requests_redirect_https`,
`stdlib/urlopen_https`). The bundled CA store is built: `create_default_context()`
trusts a vendored Mozilla root bundle (certifi, embedded as a compiled-in blob
via `scripts/vendor_cacert.py`), so `requests.get("https://...")` / `urlopen`
verify out of the box (test `ssl/tls_bundled_ca`). The `SSLWantReadError`/
`SSLWantWriteError`/`SSLZeroReturnError` subclasses ship: non-blocking
`recv`/`send` raise the want-errors, and the write path maps a `close_notify`
return to `SSLZeroReturnError` defensively (test `ssl/tls_want_read`; `recv`
keeps returning `b""` on a clean close, matching CPython's `SSLSocket.recv`).
The system trust store ships: `load_default_certs()` resolves the platform
CA bundle (`SSL_CERT_FILE` override, else well-known bundle paths) and
`create_default_context()` loads it additively with the vendored roots via
`tpy_tls_add_ca_file` (test `ssl/tls_system_ca`). Server-side TLS ships as a
public API: `SSLContext.load_cert_chain(certfile, keyfile)` +
`wrap_socket(sock, server_side=True)` (test `ssl/tls_server`). This document
is the contract for the whole track.

## Goal

Make this work, against real HTTPS servers, with
certificate verification on by default:

```python
import requests
r = requests.get("https://example.com/api")   # real TLS handshake, cert verified
print(r.status_code, r.json())
```

and the layers it rests on:

```python
import ssl, socket
ctx = ssl.create_default_context()             # CERT_REQUIRED + hostname check
s = ctx.wrap_socket(socket.create_connection(("example.com", 443)),
                    server_hostname="example.com")   # SNI + CN/SAN match
s.sendall(b"GET / HTTP/1.1\r\n...")
plaintext = s.recv(4096)                        # decrypted in userspace
```

`http.client.HTTPSConnection` sits between them; `requests` / `urlopen`
route `https://` to it on port 443.

**Scope: HTTPS client + a minimal server-side TLS surface.** No async-reactor
TLS, no broad `ssl` surface. The server path is the CPython-faithful
`SSLContext().load_cert_chain(cert, key)` + `wrap_socket(sock,
server_side=True)`; the context is role-agnostic (no `PROTOCOL_TLS_*` /
`Purpose`), `server_side=True` requires a loaded cert chain and ignores the
client-only verify/hostname config.

Deferred surface (add on demand -- none is a near-term gap):

- **Mutual TLS** -- a server verifying a client cert (`CERT_REQUIRED` +
  `load_verify_locations` on the server context). The server path currently
  ignores verify config; needs a `tls_config_server` authmode/CA wiring pass.
- **`PROTOCOL_TLS_SERVER`/`PROTOCOL_TLS_CLIENT` + `Purpose.CLIENT_AUTH`/
  `SERVER_AUTH` + `create_default_context(purpose=)`** -- TPy's context is
  role-agnostic instead.
- **`load_cert_chain(password=)`** for encrypted private keys.
- **`SSLSyscallError`/`SSLEOFError`** exception subclasses.
- A **network-gated smoke test** proving the bundled Mozilla CA store is
  wired into an accepting verification (`ssl/tls_bundled_ca` can only prove
  the blob is embedded + that an untrusted cert is rejected -- it can't
  distinguish "wired in" from "present but unwired" offline, since no cert
  chained to a real Mozilla root can be minted without network).

`tpyc/` is **not** touched -- this is pure runtime + native-binding +
stdlib work, which keeps it clear of the THIR/MIR migration moratorium.
The one shared-code change is `io.BufferedReader`'s raw-source field
(see "Read path").

## Backend: vendored mbedTLS

Vendor **mbedTLS** under `runtime/cpp/third_party/mbedtls/`, following the
PCRE2 template exactly: a `scripts/vendor_mbedtls.py` reproducer, a
`mbedtls.vendor.json` + `mbedtls.sources.txt` sidecar pair, a hand-written
facade header `runtime/cpp/include/tpy/stdlib/mbedtls_h.hpp` that mirrors
only the symbols/types we use and does **not** include the upstream C
header (macro isolation), a `tpyc/build/mbedtls.py` factory registered in
`tpyc/build/third_party.py`'s `_FACTORIES`, a `# tpy: link("mbedtls",
managed=True)` directive in the bindings module, and a
`--mbedtls=bundled|system|auto|none` CLI flag (default bundled). The
stdlib `.o` cache and exec-results markers invalidate automatically when
the vendored tree changes, because `_runtime_hash()` already covers
`third_party/`.

Why mbedTLS over OpenSSL/BoringSSL/wolfSSL: it is Apache-2.0 (clean,
permissive), ships versioned releases, is designed to embed, and has
opaque structs that suit the facade. The faster libraries each lose on a
constraint that matters more here than the throughput we will not use
(our workload is handshake-bound on small JSON requests, not bulk
throughput): OpenSSL/LibreSSL impose a system dependency or a macro-heavy
header surface; BoringSSL has no stable API/versioning and needs Go+CMake
to build; wolfSSL is GPL/commercial.

### Backend-swap seam (forward constraint)

mbedTLS-specific code stays **physically confined** to the lower layer so
a future second backend (e.g. OpenSSL) is a contained swap, not a
rewrite:

- `lib/tpy/_bindings/mbedtls.py` -- raw 1:1 `@native` FFI; the only place
  `mbedtls_*` symbols appear.
- `lib/tpy/ssl.py` -- the public, CPython-shaped, backend-agnostic
  surface. Its classes hold opaque handles and orchestrate, but every FFI
  call is confined below.

Explicitly **not** built (would be overengineering for one backend): no
runtime-pluggable backend `@dynamic` protocol, no stubbed second backend,
no abstract `SSLBackend` interface. The separation is physical (which file
the calls live in) plus a CPython-shaped public surface. The `--mbedtls`
flag generalizes to `--ssl-backend` if a second backend ever lands; not
pre-built.

## CA trust

Vendor a Mozilla CA bundle (the `cacert.pem` set, as `certifi` /
`webpki-roots` do) as the default trust store; mbedTLS ships none and does
not read the system store. Support `SSLContext.load_verify_locations(
cafile=...)` for custom CAs (e.g. a corporate root). The system trust
store is read ADDITIVELY on top (see below) -- the vendored bundle keeps
the no-store fallback hermetic and cross-platform-identical (consistent
with the test-cache assumptions).

**Built.** `scripts/vendor_cacert.py` pins a certifi release (the Mozilla NSS
root set, same source as curl's `cacert.pem`, and what CPython's `requests`
trusts by default), verifies its SHA256, and generates a C source embedding
the PEM as a string literal (`runtime/cpp/third_party/cacert/cacert_data.c`)
plus a `cacert.pem` reference copy and a `.vendor.json` sidecar. The blob is **embedded** (compiled in), not
a runtime file path: `tpyc/build/mbedtls.py` compiles `cacert_data.c` alongside
the shim (so it links exactly when mbedTLS does), and the shim parses the buffer
via `mbedtls_x509_crt_parse` (not `_parse_file`) -- hermetic, no install-layout
or cwd dependence. `SSLContext._use_bundled_ca` (set by `create_default_context()`,
cleared on a bare `SSLContext()`) gates a `tpy_tls_add_bundled_ca` call in
`wrap_socket`, which **appends** the roots to any file roots (additive, matching
CPython's `load_verify_locations`). No CLI flag: the blob is opt-in at runtime
(bare `SSLContext()` / `verify=False` / `CERT_NONE` opt out). A test hook
(`ssl._bundled_ca_count()` -> `tpy_tls_bundled_ca_count`) asserts the bundle is
embedded and non-empty offline (a real public-root handshake needs network).

**System trust store (built).** `SSLContext.load_default_certs()` (called by
`create_default_context()`) resolves the platform CA bundle -- `SSL_CERT_FILE`
env override, else the first existing path from `ssl._ca_probe_paths` (the curl/Go
well-known list: Debian/Fedora/RHEL/openSUSE/Alpine/FreeBSD/macOS/Homebrew;
a module global so tests can inject a fixture bundle) -- and `wrap_socket`
parses it via `tpy_tls_add_ca_file` (sibling of `tpy_tls_add_bundled_ca`;
`mbedtls_x509_crt_parse_file`, positive partial-parse accepted like OpenSSL)
into the same additive `s->cacert` chain. So: vendored roots UNION system
bundle UNION `load_verify_locations` -- a corporate CA installed system-wide
verifies flag-free, like curl, and machines with no store keep the hermetic
vendored behavior. Declared gaps: macOS Keychain-only CAs (database, not a
PEM file; the shipped `/etc/ssl/cert.pem` + Homebrew's exported bundle cover
common setups, `SSL_CERT_FILE` the rest), `SSL_CERT_DIR` directory stores
(mbedTLS `x509_crt_parse_path` exists -- bounded follow-up), Windows
CryptoAPI (no bundle file; lands with the Windows port). Loading is
best-effort, matching CPython/OpenSSL (verified empirically against real
CPython): a missing or unparseable bundle -- even an explicit
`SSL_CERT_FILE` -- is skipped, never raises; `load_verify_locations` is the
loud explicit spelling. Test `ssl/tls_system_ca` covers both seams: the
probe list (`ssl._ca_probe_paths`) and the in-process env snapshot
(`os.environ["SSL_CERT_FILE"] = ...`, the `ospath_expanduser` precedent),
including env-wins-over-probe, garbage-bundle skip on both branches, and
the untrusted-rejected negative; it sanitizes any inherited `SSL_CERT_FILE`
at start for hermeticity.

## I/O model: fd-direct BIO

mbedTLS does its wire I/O **directly on the socket fd** (`::send`/`::recv`
via its built-in net shim), not through TPy-level `socket.recv`/`send`.

- The callback alternative (custom BIO routing into our socket layer) is
  blocked twice over: TPy cannot yet pass a TPy function as a C callback
  pointer (`docs/NATIVE_INTEROP.md` lists it as Open), and even if it
  could, a TPy/C++ exception cannot safely unwind through mbedTLS's C
  frames -- a BIO callback must return integer codes, not raise, so it
  could not reuse socket.py's raise-based error taxonomy anyway.
- **Timeouts ride the existing model for free**: `socket.settimeout`
  already sets `SO_RCVTIMEO`/`SO_SNDTIMEO` on the fd, so a blocking
  `mbedtls_ssl_read` hits the kernel timeout and we map the result to
  `TimeoutError`, mirroring the plaintext `os.read`->EAGAIN path. No new
  timeout plumbing.
- fd-direct with `WANT_READ`/`WANT_WRITE` is also the standard
  non-blocking-TLS shape, so it is the more async-reactor-friendly choice
  if TLS ever integrates with the epoll reactor.

The one cost: socket.py's errno->exception mapping is bypassed, so `ssl.py`
carries **one TLS-aware error-mapping function** (mbedTLS code -> exception).
That is partly unavoidable regardless of I/O model, because mbedTLS
surfaces TLS-specific failures (cert verify, handshake alert,
`close_notify`) that have no socket-errno equivalent.

## Read path: `@dynamic RawBinaryIO` under `BufferedReader`

The wall: `socket.makefile()` dups the fd and `FileIO` reads it with
`os.read` -- that is **ciphertext** for a TLS socket. TLS decrypts in
userspace via `mbedtls_ssl_read`, so the raw source under `BufferedReader`
must be able to be "an mbedTLS read" instead of "an os.read." But
`HTTPResponse` holds a concrete `BufferedReader` and `BufferedReader` held
a concrete `FileIO`, so a substitute reader would slice (subclass into a
value field) or force a viral static type param up through `HTTPResponse`
-> `requests`.

Solution: make `BufferedReader`'s raw layer a small **`@dynamic`
protocol**:

```python
@dynamic
class RawBinaryIO(Protocol):       # the entire raw seam: 2 methods
    def read(self, size: int32 = -1) -> bytes: ...
    def close(self) -> None: ...

class BufferedReader(...):
    _raw: Box[RawBinaryIO]                       # was: _raw: FileIO
    def __init__(self, raw: Own[RawBinaryIO], ...):
        self._raw = Box(raw)
    def _fill(self): chunk = self._raw.read(self._buffer_size)
```

- `FileIO` conforms unchanged (it already has `read`/`close`); `ssl` adds
  `SSLRawIO` conforming (`read` -> `mbedtls_ssl_read`, `close` ->
  `close_notify` + free).
- `HTTPResponse` and `requests` are **untouched** -- the polymorphism is
  confined to this one field.
- The owning form is `Box[RawBinaryIO]` (the `Box<dyn Trait>` shape): a
  bare owning protocol field is rejected ("protocols are only valid as
  function and method parameters"), but `Box[P]` compiles, builds, and
  dispatches owned (proven by probe; see below).

Why `@dynamic`: it gives polymorphism **without a viral type param** (the
concern that drove "keep `_raw` concrete") and **without slicing**. It is
also CPython's own shape -- `BufferedReader` over a `RawIOBase` -- and
keeps `io` decoupled from `ssl` (no `mbedtls` import in `io.py`, so
`--mbedtls=none` and non-TLS programs do not link mbedTLS).

Costs: one heap alloc per `BufferedReader` + a vcall per **8 KB fill**
(negligible); and snapshot churn on the cases that snapshot
`BufferedReader`/`FileIO`/http/requests (quantified + flagged before
regenerating, per snapshot policy).

## Ownership: `Rc[SslSession]`

The one stateful TLS session is needed by both the write side
(`SSLSocket.sendall` -> `mbedtls_ssl_write`) and the read side
(`makefile()`'s `SSLRawIO` -> `mbedtls_ssl_read`, living in a
`BufferedReader` that outlives `getresponse()`). Model it as
`Rc[SslSession]`: `SSLSocket` holds one handle, `makefile()` hands
`SSLRawIO` an `Rc.clone()`. One underlying context (correct -- TLS is one
session), shared by both sides, freed in `SslSession.__del__`
(`close_notify` + free config/context/CA-chain/RNG + close fd) when the
last handle drops.

The move model (move session out of `SSLSocket` at `makefile()`) is
blocked by TPy's poor support for moving a `@nocopy` `Own` field out and
returning it. The borrow model (raw pointer from `SSLRawIO` to the
session) is an unsound stored-borrow lifetime. `Rc` is the sound model and
dodges both footguns; non-atomic `Rc` is fine (single-threaded; mbedTLS
contexts are not thread-safe anyway).

Note: plaintext `socket.makefile()` keeps its existing dup-the-fd
strategy -- TLS does not indict it (the TLS problem is "the readable thing
is not an fd at all," orthogonal to fd sharing), and the meaningful
unification is the `RawBinaryIO` seam, not the fd layer. TLS keep-alive has
since landed (persistent connections ride the shared `_Connection` path;
`tests/cases/stdlib/https_client` pins a second cycle) and dup remains
sufficient -- the drain-before-next-request discipline never reads through
socket and reader concurrently. The `Rc`-shared plaintext cleanup stays
tracked in TODO.md (io v2, the makefile shared-fd / `tplib.net.Socket`
split item).

## Security defaults (secure by default)

Match CPython's `create_default_context()` + `requests` `verify=True`:

- `CERT_REQUIRED` + `check_hostname=True`, min **TLS 1.2** (TLS 1.3 if the
  mbedTLS build enables it), mbedTLS default ciphers, trust = vendored CA
  bundle.
- Hostname verification rides SNI: `mbedtls_ssl_set_hostname` sets the SNI
  name and drives the CN/SAN match (mismatch -> `BADCERT_CN_MISMATCH`).
- Escape hatch: `requests.get(..., verify=False)` and
  `SSLContext.verify_mode = CERT_NONE` (also disables hostname check);
  `verify="<path>"` -> `load_verify_locations`.
- `SSLContext` surface: `load_verify_locations(cafile)`, `check_hostname`,
  `verify_mode`, and `load_cert_chain(certfile, keyfile)` (server identity).
  Deferred: `set_ciphers`, client-cert / mutual TLS (`load_cert_chain` is a
  no-op on the client path), CRL/OCSP, ALPN, fine-grained `minimum_version`.

## Exceptions

- `ssl.SSLError(OSError)` -- base for TLS failures (subclassing `OSError`
  matches CPython and composes with the existing socket family).
- `ssl.SSLCertVerificationError(SSLError)` -- cert verification failure
  (the one specific subclass worth having in v1). Structured
  `verify_code`/`verify_message` attrs deferred (the general
  exception-carries-only-a-message gap).
- `SSLWantReadError`/`SSLWantWriteError`/`SSLZeroReturnError(SSLError)` --
  shipped: raised by the shared `_raise_io_error` mapping on
  WANT_READ/WANT_WRITE/close_notify from `recv`/`send`/`sendall`
  (`do_handshake` keeps its bool return -- declared divergence). The
  close_notify arm is defensive on the write path (mbedTLS surfaces
  PEER_CLOSE_NOTIFY from reads; CPython's write-after-close behavior is
  unverified). Still deferred: `SSLSyscallError`, `SSLEOFError`.
- The TLS error-mapping function routes mbedTLS codes: verify failure ->
  `SSLCertVerificationError`; other TLS/handshake -> `SSLError`;
  transport read/write error/timeout -> the **existing** socket
  `ConnectionError`/`TimeoutError` family (reused, not duplicated).
- `requests`: add `requests.SSLError(ConnectionError)` and wrap
  `ssl.SSLError` in `_request_on`, mirroring the `Timeout`/`ConnectionError`
  wraps already there.

## http.client / requests / urlopen integration

- `http.client` (DONE, Increment 4): added `HTTPS_PORT = 443` and
  `HTTPSConnection`, a **sibling** of `HTTPConnection` -- NOT a subclass.
  TPy method dispatch is static, so a base-typed reference to a subclass
  would call the base method (silent breakage when a caller holds a
  connection polymorphically); instead both classes satisfy a `@dynamic
  _Connection` protocol (`connect`/`request`/`getresponse`/`close`), so a
  caller can hold either behind one `Box[_Connection]` and dispatch
  virtually. The request-building logic (`_build_request`/`_content_length`)
  is extracted to module free functions shared by both. `HTTPSConnection`
  runs `connect()` (`socket.create_connection` then `context.wrap_socket`)
  and reads the response via `SSLSocket.makefile()`; `context: SSLContext |
  None = None` defaults to `create_default_context()` (a caller context is
  captured by value).
- **Increment 5 (DONE): NOMINAL inheritance** -- `class HTTPConnection(
  _Connection)` / `class HTTPSConnection(_Connection)`. Explicit conformance
  (checked at the class, not just at each box site), and `Box[_Connection]`
  stores the conformer directly as the base -- no `Adapter` -- which sidesteps
  the structural-conformance incomplete-`Adapter` bug (BUGS.md). Cost: a vtable
  pointer per instance (concrete calls devirtualize). Two prerequisite codegen
  fixes landed first on master: (1) a @dynamic-override reference-type param was
  const-mismatched against the base pure-virtual, leaving the class abstract;
  (2) the polymorphic-slicing guard over-rejected moving a freshly-constructed
  named local into `Box[P]`, which is why a caller can now build a connection,
  set its transport field, and box the local directly (no ctor-injection seam).
- `requests._connect` (DONE): branches `parts.scheme == "https"` ->
  `HTTPSConnection` on port 443 and threads `verify: bool|str` into the context
  via `_ssl_context_for`; `requests.SSLError(ConnectionError)` wraps
  `ssl.SSLError`; `Authorization` is dropped across a host/scheme/port change
  (`should_strip_auth`). The redirect-hop scheme check and `urlopen` widen from
  `"http"`-only to `"http"`/`"https"` (`urlopen` takes a CPython-style
  `context=`). `requests`/`urlopen` construct/hold `Box[_Connection]` directly;
  the offline tests build a connection, set its `sock`/`_tls` field, then box
  the fresh local (no ctor-injection seam -- the slicing-guard fix permits it).
- The `tests/cases/tplib/requests_redirect_https` case was **rewritten** -- the
  http->https redirect is now followed over a real TLS hop (was: asserts it
  raises). New `requests_https` (direct https GET via the `Box[_Connection]`
  seam) + `stdlib/urlopen_https` cover the https path.

## Testing: step-wise handshake over `socketpair`

The exec phase runs the compiled binary, so both sides of the handshake
must exist in TPy/C++. Use a **single-threaded, step-wise real-mbedTLS
handshake over `socket.socketpair()`**:

- `a, b = socket.socketpair()`; both non-blocking. Client mbedTLS context
  on `a`, server context on `b`, each via mbedTLS's built-in fd BIO (no
  callbacks -- sidesteps the FFI gap).
- Alternate `client_handshake_step()` / `server_handshake_step()` (TLS is
  a deterministic ping-pong; each returns `WANT_READ`/`WANT_WRITE` to
  yield) until both complete, then exchange app data the same way.

Requirements / costs:
- A **server-side mbedTLS path for the test peer** (the raw bindings are
  symmetric -- `config_defaults` takes a SERVER/CLIENT flag). This is now the
  public `load_cert_chain` + `wrap_socket(server_side=True)` surface; the test
  peer drives it directly.
- A committed **self-signed test cert + key** fixture (long-dated to avoid
  expiry flakiness) in a shared fixture dir. The client trusts it via
  `load_verify_locations` and sets a matching `server_hostname`, so the
  **full verification + hostname-match** path is exercised -- plus a
  **negative** case (wrong hostname / untrusted cert -> the verify error).
- `no_cpython` (consistent with the already-`no_cpython` requests stack);
  output is decrypted app data (host-independent), so the snapshot is
  stable. The real validation is exec-phase real crypto.

Rejected alternatives: a passthrough mock SSLSocket tests nothing; byte
replay cannot reproduce a live handshake's fresh randomness; memory-BIO
loopback needs custom BIO callbacks (the FFI gap); a forked server child
needs `os.fork` (absent).

Higher layers test by injecting an already-handshaken `SSLSocket` through
the existing `_connection` seam, so handshake mechanics are not re-tested
at every layer.

## Build order

0. **Probes** (retire the Medium-confidence assumptions before building):
   (a) `@dynamic RawBinaryIO` owning field via `Box[P]` -- **DONE, PASS**
   (compiles, builds, dispatches; bare protocol field rejected, `Box[P]`
   works). (b) one C++ step-wise mbedTLS handshake round-trip over a
   non-blocking socketpair -- pending (needs mbedTLS, first task of step 1).
1. Vendoring backbone + handshake proof: vendor mbedTLS + facade + build
   wiring + `--mbedtls` flag + RNG/entropy seeding + raw bindings +
   vendored CA bundle + self-signed test cert + a bare end-to-end
   handshake test.
2. The `ssl` module: `SSLContext`/`create_default_context`/`wrap_socket`/
   `SSLSocket` (handshake/recv/send + `makefile`->`SSLRawIO`/`Rc` session)
   + verification + hostname + SNI + the error-mapping fn + the exception
   tree. Tests: handshake happy + negative cert-verify.
3. `BufferedReader` `RawBinaryIO` refactor (`_raw` -> `Box[RawBinaryIO]`);
   regenerate affected snapshots (list flagged first).
4. `HTTPSConnection` in `http.client`.
5. `requests`/`urlopen` https routing + `verify=`; rewrite
   `requests_redirect_https`.
6. Docs (`LANGUAGE_FEATURES.md`, `STDLIB_ROADMAP.md`, this doc) + file all
   deferrals.

Each step is a checkpoint commit on `feat-ssl-https`; squash-merged at the
end after `/tpy-review` + `/tpy-ready`.

## Deferred (file as TODO when the relevant step lands)

- Advanced `SSLContext` knobs: `set_ciphers`, client certs / mTLS, CRL /
  OCSP, ALPN, fine-grained `minimum_version`.
- Structured exception attributes (`verify_code`/`verify_message`, and the
  broader exception-carries-only-a-message gap).
- System trust-store integration.
- `Rc`-unify plaintext `makefile` -- TLS keep-alive has landed (its former
  precondition), but dup still suffices under the drain-before-next-request
  discipline; tracked in TODO.md (io v2 makefile shared-fd item).
- Async-reactor TLS integration. (Server-side TLS as a public API has
  shipped -- see the "Deferred surface" section above for its remaining
  sub-items: mutual TLS, `PROTOCOL_TLS_*`/`Purpose`, `password=`.)
