# tpy: native_module
# tpy: cpp_namespace("tpystd::builtins")
from .._bootstrap._decorators import readonly, Own
from .._bootstrap._extern import native
from .._core._types import ReturnException, StrView, Throwable


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

@native("tpy::OSError")
class OSError(Exception):
    def __init__(self, message: str = "") -> None: ...

@native("tpy::FileNotFoundError")
class FileNotFoundError(OSError):
    def __init__(self, message: str = "") -> None: ...

@native("tpy::PermissionError")
class PermissionError(OSError):
    def __init__(self, message: str = "") -> None: ...

# Raised on EAGAIN/EWOULDBLOCK/EINPROGRESS by non-blocking socket calls;
# the asyncio reactor catches it to park on fd readiness (CPython parity).
@native("tpy::BlockingIOError")
class BlockingIOError(OSError):
    def __init__(self, message: str = "") -> None: ...

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

@native("tpy::TimeoutError")
class TimeoutError(Exception):
    def __init__(self, message: str = "") -> None: ...

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
