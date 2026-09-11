"""
TurboPython C++ Code Generator Package

Generates C++ code from the analyzed TurboPython AST:
- Records -> structs with constructors
- Functions -> free functions
- Protocols -> C++20 concepts
- Pointer field access (.) -> arrow operator (->)
"""

from .context import CodeGenError, CodeGenOptions, stamp_codegen_error_file
from .generator import CodeGenerator

__all__ = [
    'CodeGenerator',
    'CodeGenError',
    'CodeGenOptions',
    'stamp_codegen_error_file',
]
