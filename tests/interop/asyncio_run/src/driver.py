# Shared across the ext-exec and cpy-parity runs. An `asyncio.run` inside an
# @export function returns its coroutine's result and leaves the host's SIGINT
# handling as it found it: `getsignal` still answers CPython's own handler, and
# a SIGINT sent after the run reaches CPython's handler, not the run's.
import signal

import arun


def sigint_reaches_python() -> str:
    try:
        signal.raise_signal(signal.SIGINT)
    except KeyboardInterrupt:
        return "KeyboardInterrupt"
    return "no interrupt"


print("default before", signal.getsignal(signal.SIGINT) is signal.default_int_handler)
print("result", arun.run_double(21))
print("default after", signal.getsignal(signal.SIGINT) is signal.default_int_handler)
print("raise after", sigint_reaches_python())

# A Ctrl-C inside the run cancels its root task (the run's own handler), and
# CPython's handler is back once the run returns.
print("interrupted", arun.run_interrupted())
print("default after interrupt",
      signal.getsignal(signal.SIGINT) is signal.default_int_handler)
print("raise after interrupt", sigint_reaches_python())

# A Python-level SIGINT handler the host installed stays in place, and is the
# one a SIGINT runs after the run.
hits = []


def on_sigint(signum, frame):
    hits.append(signum)


signal.signal(signal.SIGINT, on_sigint)
print("result with handler", arun.run_double(5))
print("handler kept", signal.getsignal(signal.SIGINT) is on_sigint)
signal.raise_signal(signal.SIGINT)
print("handler ran", hits == [signal.SIGINT])
signal.signal(signal.SIGINT, signal.default_int_handler)
