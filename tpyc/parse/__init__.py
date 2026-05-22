"""
TurboPython Parser package.

Re-exports all public symbols for backward compatibility.
All existing imports like ``from .parse import X`` continue to work.
"""

from .nodes import (
    ParseError, SourceLocation, RecordLinkage, FunctionLinkage, VarLinkage,
    TpyTypeRef, TpyUnionRef, TpyCallableRef, TpyLiteralRef, TpyInferFromDefaultRef,
    ResolverInputNode, TypeRefNode,
    TpyExpr, TpyIntLiteral, TpyFloatLiteral, TpyStrLiteral, TpyBytesLiteral,
    TpyFStringValue, TpyFString,
    FSTRING_CONV_NONE, FSTRING_CONV_STR, FSTRING_CONV_REPR, FSTRING_CONV_ASCII,
    TpyBoolLiteral,
    TpyNoneLiteral, TpyName, TpyBinOp, TpyChainedCompare, TpyUnaryOp, TpyTypeParamConstruct,
    TpyVarargPack, TpyStarUnpack, TpyCall, TpyMethodCall,
    TpyFieldAccess, TpyArrayLiteral, TpyTupleLiteral, TupleElemCapture, TpyDictLiteral, TpySetLiteral, TpyListRepeat,
    TpyComprehensionGenerator, TpyListComprehension, TpyDictComprehension, TpySetComprehension, TpyGeneratorExpression,
    TpySlice, TpySubscript, TpyCoerce,
    TpyIfExpr, TpyNamedExpr, TpyAwait, TpyLambda,
    TpyStmt, TpyVarDecl, TpyTupleUnpack, TpyAssign, TpyAugAssign, TpyDelItem, TpyDelVar, TpyDelAttr, TpyExprStmt, TpyReturn, TpyYield,
    TpyAssert, TpyIf, TpyWhile, TpyForEach, TpyBreak, TpyContinue,
    TpyPassStmt, TpyGlobal, TpyNonlocal, TpyRaise, TpyExceptHandler, TpyTry, TpyWithItem, TpyWith,
    TpyNestedDef,
    TpyPattern, TpyWildcardPattern, TpyCapturePattern, TpyClassPattern,
    TpyLiteralPattern, TpyValuePattern, TpyOrPattern, TpyAsPattern,
    TpyMatchCase, TpyMatch,
    RelativeImportKey, TpyImport, TpyFunction, TpyRecord, TpyProtocol, TpyEnum,
    ParseWarning, TpyModule,
    is_docstring,
    is_super_del_call,
    is_base_init_call,
    collect_name_refs,
    collect_top_level_local_names,
    expr_reads_self_field,
    is_stable_address_lvalue,
)

from .imports import (
    is_parser_keyword, _IMPLICIT_MODULES,
    get_builtins_exports, get_typing_exports, get_tpy_exports,
    scan_star_exports, NonLiteralAllError,
    ImportProcessor,
)

from .parser import Parser

__all__ = [
    # nodes
    "ParseError", "SourceLocation", "RecordLinkage", "FunctionLinkage", "VarLinkage",
    "TpyExpr", "TpyIntLiteral", "TpyFloatLiteral", "TpyStrLiteral", "TpyBytesLiteral",
    "TpyFStringValue", "TpyFString",
    "FSTRING_CONV_NONE", "FSTRING_CONV_STR", "FSTRING_CONV_REPR", "FSTRING_CONV_ASCII",
    "TpyBoolLiteral",
    "TpyNoneLiteral", "TpyName", "TpyBinOp", "TpyChainedCompare", "TpyUnaryOp", "TpyTypeParamConstruct",
    "TpyCall", "TpyMethodCall",
    "TpyFieldAccess", "TpyArrayLiteral", "TpyTupleLiteral", "TupleElemCapture", "TpyDictLiteral", "TpySetLiteral", "TpyListRepeat",
    "TpyComprehensionGenerator", "TpyListComprehension", "TpyDictComprehension", "TpySetComprehension", "TpyGeneratorExpression",
    "TpySlice", "TpySubscript", "TpyCoerce",
    "TpyIfExpr", "TpyNamedExpr", "TpyAwait", "TpyLambda",
    "TpyStmt", "TpyVarDecl", "TpyTupleUnpack", "TpyAssign", "TpyAugAssign", "TpyDelItem", "TpyDelVar", "TpyDelAttr", "TpyExprStmt", "TpyReturn", "TpyYield",
    "TpyAssert", "TpyIf", "TpyWhile", "TpyForEach", "TpyBreak", "TpyContinue",
    "TpyPassStmt", "TpyGlobal", "TpyNonlocal", "TpyRaise", "TpyExceptHandler", "TpyTry", "TpyWithItem", "TpyWith",
    "TpyNestedDef",
    "TpyPattern", "TpyWildcardPattern", "TpyCapturePattern", "TpyClassPattern",
    "TpyLiteralPattern", "TpyValuePattern", "TpyOrPattern", "TpyAsPattern",
    "TpyMatchCase", "TpyMatch",
    "RelativeImportKey", "TpyImport", "TpyFunction", "TpyRecord", "TpyProtocol", "TpyEnum",
    "ParseWarning", "TpyModule",
    "is_docstring",
    "is_super_del_call",
    "is_base_init_call",
    "collect_name_refs",
    "collect_top_level_local_names",
    "expr_reads_self_field",
    # imports
    "is_parser_keyword", "_IMPLICIT_MODULES",
    "get_builtins_exports", "get_typing_exports", "get_tpy_exports",
    "scan_star_exports", "NonLiteralAllError",
    "ImportProcessor",
    # parser
    "Parser",
]
