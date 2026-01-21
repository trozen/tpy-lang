"""
TurboPython Type System

Defines the types available in TurboPython:
- Int32: 32-bit integer (maps to int32_t)
- Ptr[T]: Mutable pointer (maps to T*)
- ConstPtr[T]: Read-only pointer (maps to const T*)
- StaticList[T, N]: Fixed-capacity container
- User-defined records (classes)
"""

from __future__ import annotations
from dataclasses import dataclass
from typing import Optional


@dataclass(frozen=True)
class TpyType:
    """Base class for all TurboPython types."""

    def to_cpp(self) -> str:
        """Return the C++ representation of this type."""
        raise NotImplementedError

    def is_pointer(self) -> bool:
        """Return True if this is a pointer type."""
        return False


@dataclass(frozen=True)
class Int32Type(TpyType):
    """32-bit integer type."""

    def to_cpp(self) -> str:
        return "int32_t"

    def __str__(self) -> str:
        return "Int32"


@dataclass(frozen=True)
class VoidType(TpyType):
    """Void type (for functions returning nothing)."""

    def to_cpp(self) -> str:
        return "void"

    def __str__(self) -> str:
        return "None"


@dataclass(frozen=True)
class StrType(TpyType):
    """String literal type (const char*)."""

    def to_cpp(self) -> str:
        return "const char*"

    def __str__(self) -> str:
        return "str"


@dataclass(frozen=True)
class RecordType(TpyType):
    """User-defined record type (class)."""
    name: str

    def to_cpp(self) -> str:
        return self.name

    def __str__(self) -> str:
        return self.name


@dataclass(frozen=True)
class PtrType(TpyType):
    """Mutable pointer type: Ptr[T] -> T*"""
    pointee: TpyType

    def to_cpp(self) -> str:
        return f"{self.pointee.to_cpp()}*"

    def is_pointer(self) -> bool:
        return True

    def __str__(self) -> str:
        return f"Ptr[{self.pointee}]"


@dataclass(frozen=True)
class ConstPtrType(TpyType):
    """Read-only pointer type: ConstPtr[T] -> const T*"""
    pointee: TpyType

    def to_cpp(self) -> str:
        return f"const {self.pointee.to_cpp()}*"

    def is_pointer(self) -> bool:
        return True

    def __str__(self) -> str:
        return f"ConstPtr[{self.pointee}]"


@dataclass(frozen=True)
class StaticListType(TpyType):
    """Fixed-capacity container: StaticList[T, N] -> StaticList<T, N>"""
    element_type: TpyType
    capacity: int

    def to_cpp(self) -> str:
        return f"StaticList<{self.element_type.to_cpp()}, {self.capacity}>"

    def __str__(self) -> str:
        return f"StaticList[{self.element_type}, {self.capacity}]"


# Singleton instances for built-in types
INT32 = Int32Type()
VOID = VoidType()
STR = StrType()


@dataclass
class FieldInfo:
    """Information about a record field."""
    name: str
    type: TpyType
    default_value: Optional[str] = None


@dataclass
class RecordInfo:
    """Information about a user-defined record (class)."""
    name: str
    fields: list[FieldInfo]
    has_init: bool = False
    init_params: list[tuple[str, TpyType, Optional[str]]] = None  # (name, type, default)
    methods: dict[str, 'FunctionInfo'] = None  # method_name -> FunctionInfo

    def __post_init__(self):
        if self.init_params is None:
            self.init_params = []
        if self.methods is None:
            self.methods = {}

    def get_method(self, name: str) -> Optional['FunctionInfo']:
        return self.methods.get(name)


@dataclass
class FunctionInfo:
    """Information about a function."""
    name: str
    params: list[tuple[str, TpyType]]  # (name, type)
    return_type: TpyType
    is_noalloc: bool = False
    is_method: bool = False


class TypeRegistry:
    """Registry of all known types and symbols."""

    def __init__(self):
        self.records: dict[str, RecordInfo] = {}
        self.functions: dict[str, FunctionInfo] = {}
        # Built-in types
        self.builtins = {"Int32", "Ptr", "ConstPtr", "StaticList"}

    def register_record(self, info: RecordInfo) -> None:
        self.records[info.name] = info

    def register_function(self, info: FunctionInfo) -> None:
        self.functions[info.name] = info

    def get_record(self, name: str) -> Optional[RecordInfo]:
        return self.records.get(name)

    def get_function(self, name: str) -> Optional[FunctionInfo]:
        return self.functions.get(name)

    def is_known_type(self, name: str) -> bool:
        return name in self.builtins or name in self.records
