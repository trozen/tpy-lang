"""THIR (Typed High-level IR).

An immutable, self-contained representation of a fully-analyzed module that
codegen consumes without referencing the SemanticAnalyzer. See docs/IR_DESIGN.md.

Covers the non-form value-scalar slice (fixed-int/bool over names, literals,
arithmetic, same-module calls, if/elif/else, while), behind a flag and
byte-identical to the AST-driven codegen path.
"""

from .dump import dump_thir
from .emit import emit_thir_body, THIRCodeGenError
from .lower import lower_function, lower_module
from .nodes import (
    THIRAssign,
    THIRBinOp,
    THIRCall,
    THIRCoerce,
    THIRExpr,
    THIRFunction,
    THIRFunctionLayout,
    THIRIf,
    THIRLiteral,
    THIRModule,
    THIRName,
    THIRNode,
    THIRParam,
    THIRReturn,
    THIRStmt,
    THIRVarDecl,
    THIRWhile,
)

__all__ = [
    "dump_thir",
    "emit_thir_body",
    "THIRCodeGenError",
    "lower_function",
    "lower_module",
    "THIRAssign",
    "THIRBinOp",
    "THIRCall",
    "THIRCoerce",
    "THIRExpr",
    "THIRFunction",
    "THIRFunctionLayout",
    "THIRIf",
    "THIRLiteral",
    "THIRModule",
    "THIRName",
    "THIRNode",
    "THIRParam",
    "THIRReturn",
    "THIRStmt",
    "THIRVarDecl",
    "THIRWhile",
]
