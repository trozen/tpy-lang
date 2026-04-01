# tpy: cpp_namespace("tpystd::builtins")
from .._typing import overload, Literal
from .._bootstrap._extern import native, builtin_type
from .._core._types import Int32


@builtin_type("builtins.TextIO")
@native("tpy::TextFile")
class TextIO:
    @native("read")
    def read(self) -> str: ...

    @native("write")
    def write(self, text: str) -> Int32: ...

    @native("readline")
    def readline(self) -> str: ...

    @native("readlines")
    def readlines(self) -> list[str]: ...

    @native("close")
    def close(self) -> None: ...

    @native("__enter__")
    def __enter__(self) -> TextIO: ...

    @native("__exit__")
    def __exit__(self, exc_type, exc_val, exc_tb) -> None: ...


@builtin_type("builtins.BinaryIO")
@native("tpy::BinaryFile")
class BinaryIO:
    @native("read")
    def read(self) -> bytes: ...

    @native("readline")
    def readline(self) -> bytes: ...

    @native("readlines")
    def readlines(self) -> list[bytes]: ...

    @native("write")
    def write(self, data: bytes) -> Int32: ...

    @native("close")
    def close(self) -> None: ...

    @native("__enter__")
    def __enter__(self) -> BinaryIO: ...

    @native("__exit__")
    def __exit__(self, exc_type, exc_val, exc_tb) -> None: ...


@overload
@native("tpy::builtin_open")
def open(path: str) -> TextIO: ...

@overload
@native("tpy::builtin_open_mode")
def open(path: str, mode: Literal[
    "r", "w", "a", "x", "rt", "wt", "at", "xt",
    "r+", "w+", "a+", "x+", "r+t", "w+t", "a+t", "x+t", "rt+", "wt+", "at+", "xt+",
]) -> TextIO: ...

@overload
@native("tpy::builtin_open_binary")
def open(path: str, mode: Literal[
    "rb", "wb", "ab", "xb",
    "r+b", "w+b", "a+b", "x+b", "rb+", "wb+", "ab+", "xb+",
]) -> BinaryIO: ...

@overload
@native("tpy::builtin_open_mode")
def open(path: str, mode: str) -> TextIO: ...

@overload
@native("tpy::builtin_open")
def open_text(path: str) -> TextIO: ...

@overload
@native("tpy::builtin_open_mode")
def open_text(path: str, mode: str) -> TextIO: ...

@overload
@native("tpy::builtin_open_binary")
def open_binary(path: str) -> BinaryIO: ...

@overload
@native("tpy::builtin_open_binary")
def open_binary(path: str, mode: str) -> BinaryIO: ...
