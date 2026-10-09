# tpy: cpp_namespace("tpystd::signal")
"""Minimal `signal` module -- `signal.signal` with a callable handler,
`default_int_handler`, `raise_signal`, and the common signal numbers.

A pure-TPy wrapper over the `posix_signal` binding. Under CPython
`import signal` resolves to the real stdlib module, so the same source
compiles and runs both ways.

    def on_term(signum: int, frame: FrameType | None) -> None:
        sys.exit(128 + signum)

    signal.signal(signal.SIGTERM, on_term)

Handlers run as in CPython: the C-level handler only records the signal,
and the TPy handler runs later on the main thread, at the next check point
-- every `print` (after its line is written), `time.sleep`, `input()`,
socket or subprocess wait, `os.kill` and `raise_signal`. A wait a handler
interrupts goes on to its deadline when the handler returns (PEP 475); an
exception the handler raises (`sys.exit`'s `SystemExit`, a
`KeyboardInterrupt`) comes out of the operation it interrupted. Several
pending signals run in ascending number order, and a signal sent twice
before a check point runs its handler once.

Inside `asyncio.run` a coroutine's check points deliver the same way, as in
CPython: the handler runs in the running coroutine, so a `try` around
`raise_signal` there catches the handler's exception, and one raised in a
spawned task fails only that task (`SystemExit` / `KeyboardInterrupt` leave
the run). A signal that arrives while the loop waits for I/O or a timer is
run by the loop, and its handler's exception leaves the run after its tasks
are cancelled.

SIGINT starts out at `default_int_handler`: a standalone program turns a
Ctrl-C into `KeyboardInterrupt`, unless SIGINT was inherited as ignored.
Any other SIGINT handler replaces that, and `signal.signal(SIGINT,
default_int_handler)` brings it back. An `asyncio.run` that starts with
SIGINT at `default_int_handler` installs a handler of its own for the run,
as CPython's Runner does: the first Ctrl-C cancels the root task (at its
next real suspension; a root that returns first ends cancelled), a later one
-- or one after the root finished -- raises `KeyboardInterrupt` where it is
delivered. It is an ordinary handler -- a `signal.signal(SIGINT, ...)`
inside the run replaces it, and the run's end puts `default_int_handler`
back only while its own is still installed, before the leftover tasks are
cancelled, so a Ctrl-C during their cleanup raises `KeyboardInterrupt`.
Inside a CPython extension the run cannot see the interpreter's own SIGINT
handler and installs its handler whatever that is
(BUGS.md#ext-asyncio-run-ignores-host-sigint-handler). A second Ctrl-C
while the first is still waiting for a check point terminates the process,
also under a user handler: it is the way out of a loop that reaches none.

Declared divergences from CPython:
  * `signal.signal` returns None, not the previous handler.
  * A handler's `frame` argument is always None.
  * Handlers run only at check points, so a pure-compute loop never runs
    one (TODO.md "Ctrl-C in a pure CPU loop stays pending").
  * A handler should do nothing beyond setting a scalar (`int` / `float` /
    `bool`, not `Ptr`) flag or raising: any write it makes to non-scalar
    module state is unchecked at the code it interrupted -- a list grown or
    shrunk under a `for` over it, an index the loop proved in range, a
    narrowed `Optional` field or `Ptr` global set to None
    (BUGS.md#signal-handler-writes-unchecked-at-check-points). The loop
    that polls such a flag has to reach a check point itself (a `print`, a
    `time.sleep`, an I/O wait): in a pure-compute loop the handler never
    runs and the flag never changes.
  * A handler that runs at a check point inside a `@noalloc` body is not
    held to `@noalloc` (BUGS.md#noalloc-body-runs-signal-handler).
  * A signal that arrives while a coroutine computes with no check point
    is delivered in the coroutine by the next check point it reaches; one
    it never reaches before it suspends leaves the signal to the loop: its
    exception leaves the run (the root task cancelled), and the task's own
    `try` around the computation never sees it, where CPython catches it
    in the task.

Not in this module yet (TODO.md "`signal` v2"): `SIG_DFL` / `SIG_IGN`,
`getsignal`, the `Handlers` enum and the `old = signal.signal(...)`
restore idiom; they need a `Callable | Handlers` union the compiler does not
carry yet (the gaps are listed at `signal()`). A handler is a module-level
function for now.

A `--no-main` host must arm the layer first
(`tpy::install_interrupt_handler()`); `signal.signal` raises ValueError
without it.

A build with `--no-signals` has no signal layer: `signal.signal` raises
ValueError there, and a SIGINT, one sent by `raise_signal` included, takes
the process's own disposition.
"""
from typing import Callable, Final

from tpy import int32
from tpy.extern import export, native, native_global
from types import FrameType
from _bindings import posix_signal

# Sourced from <signal.h> via `tpy_const_*` extern symbols (see signal_impl.cpp),
# the way socket.py sources SO_*/AF_INET6. A plain `Final[int32] = 2` would emit
# `inline constexpr int32_t SIGINT = ...`, which the libc SIGINT macro (in scope
# in every generated TU on macOS) rewrites into a malformed declaration.
SIGHUP: Final[int32] = native_global("tpy_const_sighup", binding="C")
SIGINT: Final[int32] = native_global("tpy_const_sigint", binding="C")
SIGQUIT: Final[int32] = native_global("tpy_const_sigquit", binding="C")
SIGKILL: Final[int32] = native_global("tpy_const_sigkill", binding="C")
SIGUSR1: Final[int32] = native_global("tpy_const_sigusr1", binding="C")
SIGUSR2: Final[int32] = native_global("tpy_const_sigusr2", binding="C")
SIGPIPE: Final[int32] = native_global("tpy_const_sigpipe", binding="C")
SIGALRM: Final[int32] = native_global("tpy_const_sigalrm", binding="C")
SIGTERM: Final[int32] = native_global("tpy_const_sigterm", binding="C")
SIGCHLD: Final[int32] = native_global("tpy_const_sigchld", binding="C")
SIGCONT: Final[int32] = native_global("tpy_const_sigcont", binding="C")
SIGTSTP: Final[int32] = native_global("tpy_const_sigtstp", binding="C")
SIGWINCH: Final[int32] = native_global("tpy_const_sigwinch", binding="C")

# The runtime's per-signal kinds (signal_h.hpp).
_KIND_DEFAULT_INT: Final[int32] = 1
_KIND_USER: Final[int32] = 2

# The handler of every signal whose kind is _KIND_USER.
_handlers: dict[int32, Callable[[int32, FrameType | None], None]] = {}


# Called by the runtime at a check point for each signal delivered to a
# handler of _handlers (signal_impl.cpp); the handler's exception propagates
# out of the check point.
@export("tpy_signal_dispatch", binding="C")
def _dispatch(sig: int32) -> None:
    # A copy, so a handler that replaces itself is not destroyed while it runs.
    h = _handlers[sig]
    h(sig, None)


@native("::tpy_signal_same_function")
def _same_function(a: Callable[[int32, FrameType | None], None],
                   b: Callable[[int32, FrameType | None], None]) -> bool: ...


def default_int_handler(signum: int32, frame: FrameType | None) -> None:
    """SIGINT's default handler: raise KeyboardInterrupt."""
    raise KeyboardInterrupt()


def signal(signalnum: int32,
           handler: Callable[[int32, FrameType | None], None]) -> None:
    """Run `handler(signum, None)` on the main thread when `signalnum`
    arrives (CPython's `signal.signal`, callable handlers only). Raises
    ValueError off the main thread or for a number outside 1..NSIG-1, and
    OSError(EINVAL) for SIGKILL / SIGSTOP. Returns None, where CPython
    returns the previous handler."""
    # The missing CPython surface, each blocked on the compiler:
    # - SIG_DFL / SIG_IGN / getsignal: a function or lambda is refused at a
    #   `Callable | Handlers` parameter, BUGS.md#function-to-callable-union-param
    # - the restore idiom `old = signal(...)`: that union as a local or a
    #   return value, BUGS.md#callable-enum-union-local-return-unlowered
    # - telling SIG_IGN from a handler: `h == SIG_IGN` is refused,
    #   BUGS.md#callable-enum-union-eq-enum-member
    # - `isinstance(h, Handlers)` is refused, BUGS.md#isinstance-enum-class-rejects
    # - `int(SIG_IGN)` is refused, BUGS.md#int-of-intenum-member-rejects
    # - members named SIG_DFL / SIG_IGN collide with the <signal.h> macros,
    #   BUGS.md#libc-macro-named-symbol-ill-formed; a module alias of a member
    #   is refused inside a function, BUGS.md#enum-member-module-alias-read-unlowered
    # - an enum member named like a C++ keyword is not escaped,
    #   BUGS.md#module-global-cpp-keyword-name
    # - a lambda or nested-def handler with its `FrameType | None` parameter
    #   is refused, BUGS.md#nested-def-optional-param
    # - a handler with an `int32` signum at an `int` slot fails the C++ build,
    #   BUGS.md#int32-fn-at-bigint-callable-slot (so the slot here is int32)
    # - a bound method as a handler is refused,
    #   BUGS.md#misleading-not-a-variable, and so is a lambda calling one,
    #   BUGS.md#lambda-void-call-body-unlowered
    # - `signal.default_int_handler` read through the module is refused (the
    #   from-import works), BUGS.md#module-function-as-value-rejects
    kind = _KIND_USER
    if _same_function(handler, default_int_handler):
        kind = _KIND_DEFAULT_INT
    posix_signal.enable_dispatch()
    posix_signal.set_handler(signalnum, kind)
    if kind == _KIND_USER:
        _handlers[signalnum] = handler


# asyncio.run's SIGINT handler for its run, set the way CPython's
# asyncio.Runner sets one: only over default_int_handler, and put back to it
# at the end (`_restore_default_int`) only while it is still the installed
# handler. The caller has checked that a signal is deliverable on this
# thread.
def _install_run_handler(
        handler: Callable[[int32, FrameType | None], None]) -> bool:
    if posix_signal.handler_kind(SIGINT) != _KIND_DEFAULT_INT:
        return False
    posix_signal.enable_dispatch()
    _handlers[SIGINT] = handler
    posix_signal.set_run_handler(_KIND_USER)
    return True


def _restore_default_int() -> None:
    posix_signal.set_run_handler(_KIND_DEFAULT_INT)


def raise_signal(sig: int32) -> None:
    """Send `sig` to the current process (CPython's `signal.raise_signal`).
    On the main thread its handler runs before this returns, as in CPython,
    also inside a coroutine (a SIGINT at `default_int_handler` raises
    KeyboardInterrupt; under `asyncio.run`'s own handler it cancels the
    root task); not in a `--no-signals` build."""
    posix_signal.raise_signal(sig)
    posix_signal.check_signals()
