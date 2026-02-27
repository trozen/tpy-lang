"""
TurboPython Compiler (tpyc)

A proof-of-concept compiler for TurboPython, a restricted Python-syntax
language that compiles to C++ for ultra-low-latency applications.
"""

from .typesys import (
    TpyType, Int32Type, FixedIntType, VoidType, NamedType, PtrType, is_const_ptr,
    INT32, VOID, TypeRegistry
)
from .parse import Parser, ParseError, TpyModule
from .sema import SemanticAnalyzer, SemanticError
from .codegen_cpp import CodeGenerator

__version__ = "0.1.0"
__all__ = [
    "Parser", "ParseError",
    "SemanticAnalyzer", "SemanticError",
    "CodeGenerator",
    "TpyType", "Int32Type", "FixedIntType", "VoidType", "NamedType",
    "PtrType", "is_const_ptr",
    "INT32", "VOID", "TypeRegistry", "TpyModule"
]
