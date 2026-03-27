# tpy: cpp_namespace("tpystd::builtins")
from .._typing import overload
from .._bootstrap._extern import native, cpp_template, builtin_type
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


# TODO: when overload resolution supports string literal dispatch, add
# binary mode overloads: open(path, "rb") -> BinaryIO, open(path, "wb") -> BinaryIO

@overload
@cpp_template("::tpy::builtin_open({0})")
def open(path: str) -> TextIO: ...

@overload
@cpp_template("::tpy::builtin_open_mode({0}, {1})")
def open(path: str, mode: str) -> TextIO: ...
