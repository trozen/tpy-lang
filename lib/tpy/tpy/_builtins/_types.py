# tpy: native_module
# tpy: cpp_namespace("tpystd::builtins")
from .._typing import Sized, Iterator, Iterable
from .._bootstrap._decorators import readonly, pure, error_return, Own, dispatch
from .._core._types import (
    int8, int16, int32, int64, uint8, uint16, uint32, uint64,
    char, String, StrView, float32, AnyFixedInt,
    Hashable, Representable, Stringable, NativeIterable, Truthy, Comparable, Equatable,
)
from .._bootstrap._extern import native, cpp_template, builtin_type

@builtin_type("builtins.bool")
@native("bool")
class bool(Equatable):
    @dispatch
    @cpp_template("false")
    def __init__(self) -> None: ...
    @dispatch
    @cpp_template("{0}")
    def __init__(self, x: bool) -> None: ...
    @dispatch
    @cpp_template("({0} != 0)")
    def __init__(self, x: int32) -> None: ...
    @dispatch
    @cpp_template("({0} != 0)")
    def __init__(self, x: int) -> None: ...
    @dispatch
    @cpp_template("({0} != 0.0)")
    def __init__(self, x: float) -> None: ...
    @dispatch
    @cpp_template("({0} != 0.0f)")
    def __init__(self, x: float32) -> None: ...
    @dispatch
    @cpp_template("(std::string_view({0}).size() != 0)")
    def __init__(self, x: str) -> None: ...
    @dispatch
    @native("tpy::__bool__", function=True)
    @readonly
    @pure
    def __init__(self, x: Truthy) -> None: ...

    @cpp_template("{self}")
    @readonly
    @pure
    def __bool__(self) -> bool: ...

    @cpp_template("{self} == {0}")
    @readonly
    @pure
    def __eq__(self, other: bool) -> bool: ...

    @cpp_template("::tpy::__hash__({self})")
    @readonly
    @pure
    def __hash__(self) -> uint64: ...



@builtin_type("builtins.int")
@native("::tpy::BigInt")
class int(Comparable, Equatable):
    @dispatch
    @cpp_template("::tpy::BigInt()")
    def __init__(self) -> None: ...
    @dispatch
    @cpp_template("{0}")
    def __init__(self, x: int) -> None: ...
    @dispatch
    @cpp_template("::tpy::BigInt(static_cast<int64_t>({0}))")
    def __init__(self, x: int8) -> None: ...
    @dispatch
    @cpp_template("::tpy::BigInt(static_cast<int64_t>({0}))")
    def __init__(self, x: int16) -> None: ...
    @dispatch
    @cpp_template("::tpy::BigInt(static_cast<int64_t>({0}))")
    def __init__(self, x: int32) -> None: ...
    @dispatch
    @cpp_template("::tpy::BigInt(static_cast<int64_t>({0}))")
    def __init__(self, x: int64) -> None: ...
    @dispatch
    @cpp_template("::tpy::BigInt(static_cast<uint64_t>({0}))")
    def __init__(self, x: uint8) -> None: ...
    @dispatch
    @cpp_template("::tpy::BigInt(static_cast<uint64_t>({0}))")
    def __init__(self, x: uint16) -> None: ...
    @dispatch
    @cpp_template("::tpy::BigInt(static_cast<uint64_t>({0}))")
    def __init__(self, x: uint32) -> None: ...
    @dispatch
    @cpp_template("::tpy::BigInt(static_cast<uint64_t>({0}))")
    def __init__(self, x: uint64) -> None: ...
    # from_float raises on NaN / infinity and touches no TPy state.
    @pure
    @dispatch
    @native("tpy::BigInt::from_float", function=True)
    def __init__(self, x: float) -> None: ...
    @dispatch
    @native("tpy::BigInt::from_str", function=True)
    def __init__(self, x: str) -> None: ...
    @dispatch
    @cpp_template("::tpy::BigInt(static_cast<int32_t>({0}))")
    def __init__(self, x: bool) -> None: ...
    @dispatch
    @cpp_template("::tpy::BigInt(static_cast<int32_t>({0}))")
    def __init__(self, x: char) -> None: ...
    @cpp_template("({self}) + ({0})")
    def __add__(self, other: int) -> int: ...
    @cpp_template("({self}) - ({0})")
    def __sub__(self, other: int) -> int: ...
    @cpp_template("({self}) * ({0})")
    def __mul__(self, other: int) -> int: ...
    @cpp_template("::tpy::truediv({self}, {0})")
    def __truediv__(self, other: int) -> float: ...
    @cpp_template("({self}) / ({0})")
    def __floordiv__(self, other: int) -> int: ...
    @cpp_template("({self}) % ({0})")
    def __mod__(self, other: int) -> int: ...
    @cpp_template("({self}).pow({0})")
    def __pow__(self, other: int) -> int: ...
    @cpp_template("({self}) << ({0})")
    def __lshift__(self, other: int) -> int: ...
    @cpp_template("({self}) >> ({0})")
    def __rshift__(self, other: int) -> int: ...
    @cpp_template("({self}) & ({0})")
    def __and__(self, other: int) -> int: ...
    @cpp_template("({self}) | ({0})")
    def __or__(self, other: int) -> int: ...
    @cpp_template("({self}) ^ ({0})")
    def __xor__(self, other: int) -> int: ...
    @cpp_template("({0}) + ({self})")
    def __radd__(self, other: int) -> int: ...
    @cpp_template("({0}) - ({self})")
    def __rsub__(self, other: int) -> int: ...
    @cpp_template("({0}) * ({self})")
    def __rmul__(self, other: int) -> int: ...
    @cpp_template("::tpy::truediv({0}, {self})")
    def __rtruediv__(self, other: int) -> float: ...
    @cpp_template("({0}) / ({self})")
    def __rfloordiv__(self, other: int) -> int: ...
    @cpp_template("({0}) % ({self})")
    def __rmod__(self, other: int) -> int: ...
    @cpp_template("({0}).pow({self})")
    def __rpow__(self, other: int) -> int: ...
    @cpp_template("({0}) << ({self})")
    def __rlshift__(self, other: int) -> int: ...
    @cpp_template("({0}) >> ({self})")
    def __rrshift__(self, other: int) -> int: ...
    @cpp_template("({0}) & ({self})")
    def __rand__(self, other: int) -> int: ...
    @cpp_template("({0}) | ({self})")
    def __ror__(self, other: int) -> int: ...
    @cpp_template("({0}) ^ ({self})")
    def __rxor__(self, other: int) -> int: ...
    @cpp_template("+({self})")
    def __pos__(self) -> int: ...
    @cpp_template("-({self})")
    def __neg__(self) -> int: ...
    @cpp_template("~({self})")
    def __invert__(self) -> int: ...
    @cpp_template("static_cast<bool>({self})")
    @readonly
    @pure
    def __bool__(self) -> bool: ...
    @cpp_template("::tpy::__hash__({self})")
    @readonly
    @pure
    def __hash__(self) -> uint64: ...
    @cpp_template("{self} == {0}")
    @readonly
    @pure
    def __eq__(self, other: int) -> bool: ...
    @cpp_template("{self} < {0}")
    @readonly
    @pure
    def __lt__(self, other: int) -> bool: ...
    @cpp_template("({self}).bit_length()")
    @readonly
    @pure
    def bit_length(self) -> int32: ...
    @native("tpy::bigint_as_integer_ratio", function=True)
    @readonly
    @pure
    def as_integer_ratio(self) -> tuple[int, int]: ...


@builtin_type("builtins.float")
@native("double")
class float(Comparable, Equatable):
    @dispatch
    @cpp_template("0.0")
    def __init__(self) -> None: ...
    @dispatch
    @cpp_template("{0}")
    def __init__(self, x: float) -> None: ...
    @dispatch
    @cpp_template("static_cast<double>({0})")
    def __init__(self, x: int32) -> None: ...
    @dispatch
    @cpp_template("static_cast<double>({0})")
    def __init__(self, x: int) -> None: ...
    @dispatch
    @cpp_template("static_cast<double>({0})")
    def __init__(self, x: bool) -> None: ...
    @dispatch
    @cpp_template("static_cast<double>({0})")
    def __init__(self, x: float32) -> None: ...
    @dispatch
    @cpp_template("static_cast<double>({0})")
    def __init__(self, x: int8) -> None: ...
    @dispatch
    @cpp_template("static_cast<double>({0})")
    def __init__(self, x: int16) -> None: ...
    @dispatch
    @cpp_template("static_cast<double>({0})")
    def __init__(self, x: int64) -> None: ...
    @dispatch
    @cpp_template("static_cast<double>({0})")
    def __init__(self, x: uint8) -> None: ...
    @dispatch
    @cpp_template("static_cast<double>({0})")
    def __init__(self, x: uint16) -> None: ...
    @dispatch
    @cpp_template("static_cast<double>({0})")
    def __init__(self, x: uint32) -> None: ...
    @dispatch
    @cpp_template("static_cast<double>({0})")
    def __init__(self, x: uint64) -> None: ...
    @dispatch
    @native("tpy::float_from_str", function=True)
    def __init__(self, x: str) -> None: ...
    @dispatch
    @cpp_template("({self}) + ({0})")
    def __add__(self, other: float) -> float: ...
    @dispatch
    @cpp_template("({self}) + static_cast<double>({0})")
    def __add__(self, other: int) -> float: ...
    @dispatch
    @cpp_template("({self}) + static_cast<double>({0})")
    def __add__(self, other: AnyFixedInt) -> float: ...
    @dispatch
    @cpp_template("({self}) - ({0})")
    def __sub__(self, other: float) -> float: ...
    @dispatch
    @cpp_template("({self}) - static_cast<double>({0})")
    def __sub__(self, other: int) -> float: ...
    @dispatch
    @cpp_template("({self}) - static_cast<double>({0})")
    def __sub__(self, other: AnyFixedInt) -> float: ...
    @dispatch
    @cpp_template("({self}) * ({0})")
    def __mul__(self, other: float) -> float: ...
    @dispatch
    @cpp_template("({self}) * static_cast<double>({0})")
    def __mul__(self, other: int) -> float: ...
    @dispatch
    @cpp_template("({self}) * static_cast<double>({0})")
    def __mul__(self, other: AnyFixedInt) -> float: ...
    @dispatch
    @cpp_template("::tpy::truediv({self}, {0})")
    def __truediv__(self, other: float) -> float: ...
    @dispatch
    @cpp_template("::tpy::truediv({self}, static_cast<double>({0}))")
    def __truediv__(self, other: int) -> float: ...
    @dispatch
    @cpp_template("::tpy::truediv({self}, static_cast<double>({0}))")
    def __truediv__(self, other: AnyFixedInt) -> float: ...
    @dispatch
    @cpp_template("::tpy::floordiv({self}, {0})")
    def __floordiv__(self, other: float) -> float: ...
    @dispatch
    @cpp_template("::tpy::floordiv({self}, static_cast<double>({0}))")
    def __floordiv__(self, other: int) -> float: ...
    @dispatch
    @cpp_template("::tpy::floordiv({self}, static_cast<double>({0}))")
    def __floordiv__(self, other: AnyFixedInt) -> float: ...
    @dispatch
    @cpp_template("::tpy::fmod({self}, {0})")
    def __mod__(self, other: float) -> float: ...
    @dispatch
    @cpp_template("::tpy::fmod({self}, static_cast<double>({0}))")
    def __mod__(self, other: int) -> float: ...
    @dispatch
    @cpp_template("::tpy::fmod({self}, static_cast<double>({0}))")
    def __mod__(self, other: AnyFixedInt) -> float: ...
    @dispatch
    @cpp_template("std::pow({self}, {0})")
    def __pow__(self, other: float) -> float: ...
    @dispatch
    @cpp_template("std::pow({self}, static_cast<double>({0}))")
    def __pow__(self, other: int) -> float: ...
    @dispatch
    @cpp_template("std::pow({self}, static_cast<double>({0}))")
    def __pow__(self, other: AnyFixedInt) -> float: ...
    @cpp_template("+({self})")
    def __pos__(self) -> float: ...
    @cpp_template("-({self})")
    def __neg__(self) -> float: ...
    @dispatch
    @cpp_template("({0}) + ({self})")
    def __radd__(self, other: float) -> float: ...
    @dispatch
    @cpp_template("static_cast<double>({0}) + ({self})")
    def __radd__(self, other: int) -> float: ...
    @dispatch
    @cpp_template("static_cast<double>({0}) + ({self})")
    def __radd__(self, other: AnyFixedInt) -> float: ...
    @dispatch
    @cpp_template("({0}) - ({self})")
    def __rsub__(self, other: float) -> float: ...
    @dispatch
    @cpp_template("static_cast<double>({0}) - ({self})")
    def __rsub__(self, other: int) -> float: ...
    @dispatch
    @cpp_template("static_cast<double>({0}) - ({self})")
    def __rsub__(self, other: AnyFixedInt) -> float: ...
    @dispatch
    @cpp_template("({0}) * ({self})")
    def __rmul__(self, other: float) -> float: ...
    @dispatch
    @cpp_template("static_cast<double>({0}) * ({self})")
    def __rmul__(self, other: int) -> float: ...
    @dispatch
    @cpp_template("static_cast<double>({0}) * ({self})")
    def __rmul__(self, other: AnyFixedInt) -> float: ...
    @dispatch
    @cpp_template("::tpy::truediv({0}, {self})")
    def __rtruediv__(self, other: float) -> float: ...
    @dispatch
    @cpp_template("::tpy::truediv(static_cast<double>({0}), {self})")
    def __rtruediv__(self, other: int) -> float: ...
    @dispatch
    @cpp_template("::tpy::truediv(static_cast<double>({0}), {self})")
    def __rtruediv__(self, other: AnyFixedInt) -> float: ...
    @dispatch
    @cpp_template("::tpy::floordiv({0}, {self})")
    def __rfloordiv__(self, other: float) -> float: ...
    @dispatch
    @cpp_template("::tpy::floordiv(static_cast<double>({0}), {self})")
    def __rfloordiv__(self, other: int) -> float: ...
    @dispatch
    @cpp_template("::tpy::floordiv(static_cast<double>({0}), {self})")
    def __rfloordiv__(self, other: AnyFixedInt) -> float: ...
    @dispatch
    @cpp_template("::tpy::fmod({0}, {self})")
    def __rmod__(self, other: float) -> float: ...
    @dispatch
    @cpp_template("::tpy::fmod(static_cast<double>({0}), {self})")
    def __rmod__(self, other: int) -> float: ...
    @dispatch
    @cpp_template("::tpy::fmod(static_cast<double>({0}), {self})")
    def __rmod__(self, other: AnyFixedInt) -> float: ...
    @dispatch
    @cpp_template("std::pow({0}, {self})")
    def __rpow__(self, other: float) -> float: ...
    @dispatch
    @cpp_template("std::pow(static_cast<double>({0}), {self})")
    def __rpow__(self, other: int) -> float: ...
    @dispatch
    @cpp_template("std::pow(static_cast<double>({0}), {self})")
    def __rpow__(self, other: AnyFixedInt) -> float: ...
    @cpp_template("({self} != 0.0)")
    @readonly
    @pure
    def __bool__(self) -> bool: ...
    @cpp_template("::tpy::__hash__({self})")
    @readonly
    @pure
    def __hash__(self) -> uint64: ...
    @cpp_template("{self} == {0}")
    @readonly
    @pure
    def __eq__(self, other: float) -> bool: ...
    @cpp_template("{self} < {0}")
    @readonly
    @pure
    def __lt__(self, other: float) -> bool: ...
    @native("tpy::float_as_integer_ratio", function=True)
    @readonly
    @pure
    def as_integer_ratio(self) -> tuple[int, int]: ...
@builtin_type("builtins.str")
@native("std::string")
class str(NativeIterable[char], Iterable[char], Comparable, Equatable):
    @dispatch
    @cpp_template("std::string()")
    def __init__(self) -> None: ...
    @dispatch
    @cpp_template("std::string({0})")
    def __init__(self, x: str) -> None: ...
    @dispatch
    @cpp_template("std::string(::tpy::bool_to_str({0}))")
    def __init__(self, x: bool) -> None: ...
    @dispatch
    @cpp_template("std::string(::tpy::char_to_str({0}))")
    def __init__(self, x: char) -> None: ...
    @dispatch
    @cpp_template("::tpy::fixed_to_str<int8_t>({0})")
    def __init__(self, x: int8) -> None: ...
    @dispatch
    @cpp_template("::tpy::fixed_to_str<int16_t>({0})")
    def __init__(self, x: int16) -> None: ...
    @dispatch
    @cpp_template("::tpy::fixed_to_str<int32_t>({0})")
    def __init__(self, x: int32) -> None: ...
    @dispatch
    @cpp_template("::tpy::fixed_to_str<int64_t>({0})")
    def __init__(self, x: int64) -> None: ...
    @dispatch
    @cpp_template("::tpy::fixed_to_str<uint8_t>({0})")
    def __init__(self, x: uint8) -> None: ...
    @dispatch
    @cpp_template("::tpy::fixed_to_str<uint16_t>({0})")
    def __init__(self, x: uint16) -> None: ...
    @dispatch
    @cpp_template("::tpy::fixed_to_str<uint32_t>({0})")
    def __init__(self, x: uint32) -> None: ...
    @dispatch
    @cpp_template("::tpy::fixed_to_str<uint64_t>({0})")
    def __init__(self, x: uint64) -> None: ...
    @dispatch
    @cpp_template("({0}).to_string()")
    def __init__(self, x: int) -> None: ...
    @dispatch
    @native("tpy::float_to_str", function=True)
    def __init__(self, x: float) -> None: ...
    @dispatch
    @cpp_template("::tpy::float_to_str(static_cast<double>({0}))")
    def __init__(self, x: float32) -> None: ...
    @dispatch
    @cpp_template("std::string(::tpy::__str__({0}))")
    @readonly
    @pure
    def __init__(self, x: Stringable) -> None: ...

    @cpp_template("::tpy::__iter__({self})")
    @readonly
    @pure
    def __iter__(self) -> Iterator[char]: ...

    @cpp_template("static_cast<int32_t>({self}.size())")
    @readonly
    @pure
    def __len__(self) -> int32: ...

    @dispatch
    @cpp_template("::tpy::__getitem__({self}, {0})")
    @readonly
    @pure
    def __getitem__(self, index: int32) -> char: ...

    @dispatch
    @cpp_template("::tpy::str_slice({self}, {0})")
    @readonly
    @pure
    def __getitem__(self, index: basic_slice) -> StrView: ...

    @dispatch
    @cpp_template("::tpy::str_stepped_slice({self}, {0})")
    @readonly
    @pure
    def __getitem__(self, index: slice) -> str: ...

    @dispatch
    @cpp_template("::tpy::str_concat({self}, {0})")
    def __add__(self, other: str) -> String: ...
    @dispatch
    @cpp_template("::tpy::str_concat({self}, {0})")
    def __add__(self, other: String) -> String: ...
    @dispatch
    @cpp_template("::tpy::str_concat({self}, {0})")
    def __add__(self, other: StrView) -> String: ...
    @dispatch
    @cpp_template("::tpy::str_concat({self}, ::tpy::char_to_str({0}))")
    def __add__(self, other: char) -> String: ...

    @cpp_template("::tpy::str_repeat({self}, {0})")
    @readonly
    @pure
    def __mul__(self, n: int32) -> str: ...

    @cpp_template("::tpy::str_repeat({self}, {0})")
    @readonly
    @pure
    def __rmul__(self, n: int32) -> str: ...

    @dispatch
    @cpp_template("::tpy::str_split_whitespace({self})")
    @readonly
    @pure
    def split(self) -> Own[list[str]]: ...
    @dispatch
    @cpp_template("::tpy::str_split({self}, {0})")
    @readonly
    @pure
    def split(self, sep: str) -> Own[list[str]]: ...
    @dispatch
    @cpp_template("::tpy::str_split({self}, {0}, {1})")
    @readonly
    @pure
    def split(self, sep: str, maxsplit: int32) -> Own[list[str]]: ...

    @cpp_template("::tpy::str_join({self}, {0})")
    @readonly
    @pure
    def join(self, items: Iterable[str]) -> str: ...

    @cpp_template("::tpy::str_strip({self})")
    @readonly
    @pure
    def strip(self) -> StrView: ...

    @cpp_template("::tpy::str_lstrip({self})")
    @readonly
    @pure
    def lstrip(self) -> StrView: ...

    @cpp_template("::tpy::str_rstrip({self})")
    @readonly
    @pure
    def rstrip(self) -> StrView: ...

    @cpp_template("::tpy::str_replace({self}, {0}, {1})")
    @readonly
    @pure
    def replace(self, old: str, new: str) -> str: ...

    @cpp_template("::tpy::str_find({self}, {0})")
    @readonly
    @pure
    def find(self, sub: str) -> int32: ...

    @cpp_template("::tpy::str_rfind({self}, {0})")
    @readonly
    @pure
    def rfind(self, sub: str) -> int32: ...

    @cpp_template("::tpy::str_index({self}, {0})")
    @readonly
    @pure
    def index(self, sub: str) -> int32: ...

    @cpp_template("::tpy::str_startswith({self}, {0})")
    @readonly
    @pure
    def startswith(self, prefix: str) -> bool: ...

    @cpp_template("::tpy::str_endswith({self}, {0})")
    @readonly
    @pure
    def endswith(self, suffix: str) -> bool: ...

    @cpp_template("::tpy::str_upper({self})")
    @readonly
    @pure
    def upper(self) -> str: ...

    @cpp_template("::tpy::str_lower({self})")
    @readonly
    @pure
    def lower(self) -> str: ...

    @cpp_template("::tpy::str_count({self}, {0})")
    @readonly
    @pure
    def count(self, sub: str) -> int32: ...

    @cpp_template("::tpy::str_isdigit({self})")
    @readonly
    @pure
    def isdigit(self) -> bool: ...

    @cpp_template("::tpy::str_isalpha({self})")
    @readonly
    @pure
    def isalpha(self) -> bool: ...

    @cpp_template("::tpy::str_isalnum({self})")
    @readonly
    @pure
    def isalnum(self) -> bool: ...

    @cpp_template("::tpy::str_isspace({self})")
    @readonly
    @pure
    def isspace(self) -> bool: ...

    @cpp_template("::tpy::str_isupper({self})")
    @readonly
    @pure
    def isupper(self) -> bool: ...

    @cpp_template("::tpy::str_islower({self})")
    @readonly
    @pure
    def islower(self) -> bool: ...

    @cpp_template("::tpy::str_capitalize({self})")
    @readonly
    @pure
    def capitalize(self) -> str: ...

    @cpp_template("::tpy::str_title({self})")
    @readonly
    @pure
    def title(self) -> str: ...

    @cpp_template("::tpy::str_swapcase({self})")
    @readonly
    @pure
    def swapcase(self) -> str: ...

    @cpp_template("::tpy::str_removeprefix({self}, {0})")
    @readonly
    @pure
    def removeprefix(self, prefix: str) -> StrView: ...

    @cpp_template("::tpy::str_removesuffix({self}, {0})")
    @readonly
    @pure
    def removesuffix(self, suffix: str) -> StrView: ...

    @cpp_template("::tpy::str_rindex({self}, {0})")
    @readonly
    @pure
    def rindex(self, sub: str) -> int32: ...

    @cpp_template("::tpy::str_splitlines({self})")
    @readonly
    @pure
    def splitlines(self) -> Own[list[str]]: ...

    @native("tpy::bytes_from_str", function=True)
    @readonly
    @pure
    def encode(self) -> bytes: ...

    @cpp_template("(!{self}.empty())")
    @readonly
    @pure
    def __bool__(self) -> bool: ...

    @cpp_template("::tpy::__hash__({self})")
    @readonly
    @pure
    def __hash__(self) -> uint64: ...

    @cpp_template("{self} == {0}")
    @readonly
    @pure
    def __eq__(self, other: str) -> bool: ...

    @cpp_template("{self} < {0}")
    @readonly
    @pure
    def __lt__(self, other: str) -> bool: ...


@builtin_type("tpy.basic_slice")
@native("tpy::BasicSlice")
class basic_slice:
    @cpp_template("::tpy::BasicSlice{{{0}, {1}}}")
    @pure
    def __init__(self, start: int32 | None, stop: int32 | None) -> None: ...


@builtin_type("builtins.slice")
@native("tpy::Slice")
class slice:
    @cpp_template("::tpy::Slice{{{0}, {1}, {2}}}")
    @pure
    def __init__(self, start: int32 | None, stop: int32 | None, step: int32 | None) -> None: ...
