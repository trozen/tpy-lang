"""THIR (Typed High-level IR).

An immutable, self-contained representation of a fully-analyzed module that
codegen consumes without referencing the SemanticAnalyzer. See docs/IR_DESIGN.md.

Covers the non-form value-scalar slice (fixed-int/bool over names, literals,
arithmetic, same-module calls, if/elif/else, while), behind a flag and
byte-identical to the AST-driven codegen path.
"""

from .dump import dump_thir
from .emit import emit_thir_body, emit_thir_constructor_tail, THIRCodeGenError
from .lower import lower_constructor, lower_function, lower_module
from .nodes import (
    Form,
    THIRAssign,
    THIRBinOp,
    THIRCall,
    THIRCoerce,
    THIRConstructor,
    THIRExpr,
    THIRFieldAccess,
    THIRForRange,
    THIRFormConvert,
    THIRFunction,
    THIRFunctionLayout,
    THIRIf,
    THIRLiteral,
    THIRMilInit,
    THIRModule,
    THIRName,
    THIRNode,
    THIRParam,
    THIRReturn,
    THIRSelf,
    THIRStmt,
    THIRSubscript,
    THIRVarDecl,
    THIRWhile,
)

__all__ = [
    "dump_thir",
    "emit_thir_body",
    "emit_thir_constructor_tail",
    "THIRCodeGenError",
    "lower_constructor",
    "lower_function",
    "lower_module",
    "Form",
    "THIRAssign",
    "THIRBinOp",
    "THIRCall",
    "THIRCoerce",
    "THIRConstructor",
    "THIRExpr",
    "THIRFieldAccess",
    "THIRForRange",
    "THIRFormConvert",
    "THIRFunction",
    "THIRFunctionLayout",
    "THIRIf",
    "THIRLiteral",
    "THIRMilInit",
    "THIRModule",
    "THIRName",
    "THIRNode",
    "THIRParam",
    "THIRReturn",
    "THIRSelf",
    "THIRStmt",
    "THIRSubscript",
    "THIRVarDecl",
    "THIRWhile",
]
