# tpy: native_module
# tpy: cpp_namespace("tpystd::builtins")
from .._bootstrap._decorators import readonly, Own, dispatch
from .._bootstrap._extern import cpp_template, native, native_field, virtual_raise
from .._core._types import int32, ReturnException, StrView, Throwable


# Python exception hierarchy (maps to ::tpy:: runtime structs in core.hpp).
# The class-level @native is sufficient for both `raise X("msg")` and
# `e = X("msg")` codegen paths, so __init__ stubs don't need their own
# @native(..., function=True) annotation.
#
# `clone` / `__raise__` are Throwable protocol overrides; the C++ overrides
# come from TPY_THROWABLE_VIRTUALS in `runtime/cpp/include/tpy/throwable.hpp`
# applied to every native subclass. Declared once on BaseException so the
# whole hierarchy inherits the sema-level method visibility.
@native("tpy::BaseException")
class BaseException(Throwable):
    message: str

    def __init__(self, message: str = "") -> None: ...

    def __str__(self) -> StrView: ...

    # Returns Own[Throwable] DELIBERATELY -- do NOT narrow to
    # Own[BaseException]. Narrowing (so `Box(e.clone())` could be typed
    # `Box[BaseException]` instead of `Box[Throwable]`) requires covariant
    # return support, which C++ does not provide for `std::unique_ptr`
    # (only raw pointers/references; see [class.virtual] and P0670's
    # rejection). The full exploration -- a sema covariant-return rule + a
    # `tpy::narrowing_cast<>` codegen bridge -- was built and then dropped:
    # it forced a divergence between the TPy declaration and the emitted
    # C++ signature, for what amounts to a naming preference. The
    # `Box[Throwable]` + virtual-`__raise__` convention (Phase 20; see
    # `tests/cases/exceptions/box_throwable_preserves_dynamic_type`)
    # already stores exceptions polymorphically and recovers the concrete
    # subclass via `raise stored / except ConcreteType`. Revisit only when
    # TPy gains a backend below the C++ language layer (LLVM / the THIR-MIR
    # migration) -- see `docs/IR_DESIGN.md` Open Question 10.
    @readonly
    def clone(self) -> Own[Throwable]: ...

    @readonly
    def __raise__(self) -> None: ...

@native("tpy::Exception")
class Exception(BaseException):
    def __init__(self, message: str = "") -> None: ...

@native("tpy::ValueError")
class ValueError(Exception):
    def __init__(self, message: str = "") -> None: ...

# Carries CPython's structured `.errno` / `.strerror` / `.filename`
# attributes (0 / "" when unset -- TPy has no None default here). The C++
# members are renamed where `errno` is a C macro. The errno-taking ctors
# format the message to CPython's exact str(e) at construction time and
# record the PEP 3151 subclass CPython's __new__ would construct; RAISING
# the object surfaces that subclass via the dispatching C++ __raise__
# (hence @virtual_raise). See the runtime struct comment for the declared
# post-hoc-mutation divergence.
@virtual_raise
@native("tpy::OSError")
class OSError(Exception):
    errno: int32 = native_field("error_number")
    strerror: str = native_field("strerror_text")
    filename: str
    filename2: str

    @dispatch
    @cpp_template("tpy::OSError()")
    def __init__(self) -> None: ...

    @dispatch
    @cpp_template("tpy::OSError({0})")
    def __init__(self, message: str) -> None: ...

    @dispatch
    @cpp_template("tpy::OSError({0}, {1})")
    def __init__(self, errno: int32, strerror: str) -> None: ...

    @dispatch
    @cpp_template("tpy::OSError({0}, {1}, {2})")
    def __init__(self, errno: int32, strerror: str, filename: str) -> None: ...

@native("tpy::FileNotFoundError")
class FileNotFoundError(OSError):
    @dispatch
    @cpp_template("tpy::FileNotFoundError()")
    def __init__(self) -> None: ...

    @dispatch
    @cpp_template("tpy::FileNotFoundError({0})")
    def __init__(self, message: str) -> None: ...

    @dispatch
    @cpp_template("tpy::FileNotFoundError({0}, {1})")
    def __init__(self, errno: int32, strerror: str) -> None: ...

    @dispatch
    @cpp_template("tpy::FileNotFoundError({0}, {1}, {2})")
    def __init__(self, errno: int32, strerror: str, filename: str) -> None: ...

@native("tpy::PermissionError")
class PermissionError(OSError):
    @dispatch
    @cpp_template("tpy::PermissionError()")
    def __init__(self) -> None: ...

    @dispatch
    @cpp_template("tpy::PermissionError({0})")
    def __init__(self, message: str) -> None: ...

    @dispatch
    @cpp_template("tpy::PermissionError({0}, {1})")
    def __init__(self, errno: int32, strerror: str) -> None: ...

    @dispatch
    @cpp_template("tpy::PermissionError({0}, {1}, {2})")
    def __init__(self, errno: int32, strerror: str, filename: str) -> None: ...

@native("tpy::FileExistsError")
class FileExistsError(OSError):
    @dispatch
    @cpp_template("tpy::FileExistsError()")
    def __init__(self) -> None: ...

    @dispatch
    @cpp_template("tpy::FileExistsError({0})")
    def __init__(self, message: str) -> None: ...

    @dispatch
    @cpp_template("tpy::FileExistsError({0}, {1})")
    def __init__(self, errno: int32, strerror: str) -> None: ...

    @dispatch
    @cpp_template("tpy::FileExistsError({0}, {1}, {2})")
    def __init__(self, errno: int32, strerror: str, filename: str) -> None: ...

@native("tpy::NotADirectoryError")
class NotADirectoryError(OSError):
    @dispatch
    @cpp_template("tpy::NotADirectoryError()")
    def __init__(self) -> None: ...

    @dispatch
    @cpp_template("tpy::NotADirectoryError({0})")
    def __init__(self, message: str) -> None: ...

    @dispatch
    @cpp_template("tpy::NotADirectoryError({0}, {1})")
    def __init__(self, errno: int32, strerror: str) -> None: ...

    @dispatch
    @cpp_template("tpy::NotADirectoryError({0}, {1}, {2})")
    def __init__(self, errno: int32, strerror: str, filename: str) -> None: ...

@native("tpy::IsADirectoryError")
class IsADirectoryError(OSError):
    @dispatch
    @cpp_template("tpy::IsADirectoryError()")
    def __init__(self) -> None: ...

    @dispatch
    @cpp_template("tpy::IsADirectoryError({0})")
    def __init__(self, message: str) -> None: ...

    @dispatch
    @cpp_template("tpy::IsADirectoryError({0}, {1})")
    def __init__(self, errno: int32, strerror: str) -> None: ...

    @dispatch
    @cpp_template("tpy::IsADirectoryError({0}, {1}, {2})")
    def __init__(self, errno: int32, strerror: str, filename: str) -> None: ...

# Connection-related OSError subclasses (PEP 3151).
@native("tpy::ConnectionError")
class ConnectionError(OSError):
    @dispatch
    @cpp_template("tpy::ConnectionError()")
    def __init__(self) -> None: ...

    @dispatch
    @cpp_template("tpy::ConnectionError({0})")
    def __init__(self, message: str) -> None: ...

    @dispatch
    @cpp_template("tpy::ConnectionError({0}, {1})")
    def __init__(self, errno: int32, strerror: str) -> None: ...

    @dispatch
    @cpp_template("tpy::ConnectionError({0}, {1}, {2})")
    def __init__(self, errno: int32, strerror: str, filename: str) -> None: ...

@native("tpy::BrokenPipeError")
class BrokenPipeError(ConnectionError):
    @dispatch
    @cpp_template("tpy::BrokenPipeError()")
    def __init__(self) -> None: ...

    @dispatch
    @cpp_template("tpy::BrokenPipeError({0})")
    def __init__(self, message: str) -> None: ...

    @dispatch
    @cpp_template("tpy::BrokenPipeError({0}, {1})")
    def __init__(self, errno: int32, strerror: str) -> None: ...

    @dispatch
    @cpp_template("tpy::BrokenPipeError({0}, {1}, {2})")
    def __init__(self, errno: int32, strerror: str, filename: str) -> None: ...

@native("tpy::ConnectionResetError")
class ConnectionResetError(ConnectionError):
    @dispatch
    @cpp_template("tpy::ConnectionResetError()")
    def __init__(self) -> None: ...

    @dispatch
    @cpp_template("tpy::ConnectionResetError({0})")
    def __init__(self, message: str) -> None: ...

    @dispatch
    @cpp_template("tpy::ConnectionResetError({0}, {1})")
    def __init__(self, errno: int32, strerror: str) -> None: ...

    @dispatch
    @cpp_template("tpy::ConnectionResetError({0}, {1}, {2})")
    def __init__(self, errno: int32, strerror: str, filename: str) -> None: ...

@native("tpy::ConnectionRefusedError")
class ConnectionRefusedError(ConnectionError):
    @dispatch
    @cpp_template("tpy::ConnectionRefusedError()")
    def __init__(self) -> None: ...

    @dispatch
    @cpp_template("tpy::ConnectionRefusedError({0})")
    def __init__(self, message: str) -> None: ...

    @dispatch
    @cpp_template("tpy::ConnectionRefusedError({0}, {1})")
    def __init__(self, errno: int32, strerror: str) -> None: ...

    @dispatch
    @cpp_template("tpy::ConnectionRefusedError({0}, {1}, {2})")
    def __init__(self, errno: int32, strerror: str, filename: str) -> None: ...

@native("tpy::ConnectionAbortedError")
class ConnectionAbortedError(ConnectionError):
    @dispatch
    @cpp_template("tpy::ConnectionAbortedError()")
    def __init__(self) -> None: ...

    @dispatch
    @cpp_template("tpy::ConnectionAbortedError({0})")
    def __init__(self, message: str) -> None: ...

    @dispatch
    @cpp_template("tpy::ConnectionAbortedError({0}, {1})")
    def __init__(self, errno: int32, strerror: str) -> None: ...

    @dispatch
    @cpp_template("tpy::ConnectionAbortedError({0}, {1}, {2})")
    def __init__(self, errno: int32, strerror: str, filename: str) -> None: ...

# Raised on EAGAIN/EWOULDBLOCK/EINPROGRESS by non-blocking socket calls;
# the asyncio reactor catches it to park on fd readiness (CPython parity).
@native("tpy::BlockingIOError")
class BlockingIOError(OSError):
    @dispatch
    @cpp_template("tpy::BlockingIOError()")
    def __init__(self) -> None: ...

    @dispatch
    @cpp_template("tpy::BlockingIOError({0})")
    def __init__(self, message: str) -> None: ...

    @dispatch
    @cpp_template("tpy::BlockingIOError({0}, {1})")
    def __init__(self, errno: int32, strerror: str) -> None: ...

    @dispatch
    @cpp_template("tpy::BlockingIOError({0}, {1}, {2})")
    def __init__(self, errno: int32, strerror: str, filename: str) -> None: ...

# ECHILD: a wait on a process that is not (or no longer) our child.
@native("tpy::ChildProcessError")
class ChildProcessError(OSError):
    @dispatch
    @cpp_template("tpy::ChildProcessError()")
    def __init__(self) -> None: ...

    @dispatch
    @cpp_template("tpy::ChildProcessError({0})")
    def __init__(self, message: str) -> None: ...

    @dispatch
    @cpp_template("tpy::ChildProcessError({0}, {1})")
    def __init__(self, errno: int32, strerror: str) -> None: ...

    @dispatch
    @cpp_template("tpy::ChildProcessError({0}, {1}, {2})")
    def __init__(self, errno: int32, strerror: str, filename: str) -> None: ...

# EINTR. The os/io wrappers retry interrupted calls (PEP 475), so this
# surfaces only from calls CPython does not retry either.
@native("tpy::InterruptedError")
class InterruptedError(OSError):
    @dispatch
    @cpp_template("tpy::InterruptedError()")
    def __init__(self) -> None: ...

    @dispatch
    @cpp_template("tpy::InterruptedError({0})")
    def __init__(self, message: str) -> None: ...

    @dispatch
    @cpp_template("tpy::InterruptedError({0}, {1})")
    def __init__(self, errno: int32, strerror: str) -> None: ...

    @dispatch
    @cpp_template("tpy::InterruptedError({0}, {1}, {2})")
    def __init__(self, errno: int32, strerror: str, filename: str) -> None: ...

# ESRCH: os.kill of a process that does not exist.
@native("tpy::ProcessLookupError")
class ProcessLookupError(OSError):
    @dispatch
    @cpp_template("tpy::ProcessLookupError()")
    def __init__(self) -> None: ...

    @dispatch
    @cpp_template("tpy::ProcessLookupError({0})")
    def __init__(self, message: str) -> None: ...

    @dispatch
    @cpp_template("tpy::ProcessLookupError({0}, {1})")
    def __init__(self, errno: int32, strerror: str) -> None: ...

    @dispatch
    @cpp_template("tpy::ProcessLookupError({0}, {1}, {2})")
    def __init__(self, errno: int32, strerror: str, filename: str) -> None: ...

@native("tpy::AttributeError")
class AttributeError(Exception):
    def __init__(self, message: str = "") -> None: ...

@native("tpy::AssertionError")
class AssertionError(Exception):
    def __init__(self, message: str = "") -> None: ...

@native("tpy::LookupError")
class LookupError(Exception):
    def __init__(self, message: str = "") -> None: ...

@native("tpy::IndexError")
class IndexError(LookupError):
    def __init__(self, message: str = "") -> None: ...

@native("tpy::KeyError")
class KeyError(LookupError):
    def __init__(self, message: str = "") -> None: ...

@native("tpy::ArithmeticError")
class ArithmeticError(Exception):
    def __init__(self, message: str = "") -> None: ...

@native("tpy::ZeroDivisionError")
class ZeroDivisionError(ArithmeticError):
    def __init__(self, message: str = "") -> None: ...

@native("tpy::OverflowError")
class OverflowError(ArithmeticError):
    def __init__(self, message: str = "") -> None: ...

@native("tpy::FloatingPointError")
class FloatingPointError(ArithmeticError):
    def __init__(self, message: str = "") -> None: ...

@native("tpy::TypeError")
class TypeError(Exception):
    def __init__(self, message: str = "") -> None: ...

@native("tpy::NotImplementedError")
class NotImplementedError(Exception):
    def __init__(self, message: str = "") -> None: ...

@native("tpy::RuntimeError")
class RuntimeError(Exception):
    def __init__(self, message: str = "") -> None: ...

@native("tpy::RecursionError")
class RecursionError(RuntimeError):
    def __init__(self, message: str = "") -> None: ...

@native("tpy::EOFError")
class EOFError(Exception):
    def __init__(self, message: str = "") -> None: ...

@native("tpy::MemoryError")
class MemoryError(Exception):
    def __init__(self, message: str = "") -> None: ...

@native("tpy::StopIteration")
class StopIteration(Exception, ReturnException): ...

@native("tpy::StopAsyncIteration")
class StopAsyncIteration(Exception):
    def __init__(self, message: str = "") -> None: ...

# Subclasses OSError (not plain Exception) to match CPython, where
# `socket.timeout is TimeoutError` and `TimeoutError` is an OSError: code
# written `except OSError` catches socket/connection timeouts unchanged.
@native("tpy::TimeoutError")
class TimeoutError(OSError):
    @dispatch
    @cpp_template("tpy::TimeoutError()")
    def __init__(self) -> None: ...

    @dispatch
    @cpp_template("tpy::TimeoutError({0})")
    def __init__(self, message: str) -> None: ...

    @dispatch
    @cpp_template("tpy::TimeoutError({0}, {1})")
    def __init__(self, errno: int32, strerror: str) -> None: ...

    @dispatch
    @cpp_template("tpy::TimeoutError({0}, {1}, {2})")
    def __init__(self, errno: int32, strerror: str, filename: str) -> None: ...

# CancelledError inherits BaseException directly (not Exception) so
# `except Exception` does not silently swallow it -- matches CPython 3.8+.
# Code that wants cleanup-then-propagate on cancellation should use
# try/finally; code that intentionally consumes cancellation catches it
# explicitly. Thrown into a coroutine at its resumed-await position when
# its Task is cancelled. Re-exported by `tpy.task`.
@native("tpy::CancelledError")
class CancelledError(BaseException):
    def __init__(self, message: str = "") -> None: ...

# GeneratorExit inherits BaseException directly (not Exception), like
# CPython, so `except Exception` does not swallow a generator close.
# Passed as exc_val to with.__exit__ when an abandoned generator/
# coroutine frame's destructor closes a suspended `with` region.
@native("tpy::GeneratorExit")
class GeneratorExit(BaseException):
    def __init__(self, message: str = "") -> None: ...

# Inherits BaseException directly (not Exception), like CPython, so
# `except Exception` does not swallow a Ctrl-C. Surfaced on an uncaught SIGINT
# graceful shutdown (see asyncio.run).
@native("tpy::KeyboardInterrupt")
class KeyboardInterrupt(BaseException):
    def __init__(self, message: str = "") -> None: ...
