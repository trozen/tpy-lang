"""
TurboPython-specific types (tpy module).

Defines types like Array, Span, Int32, etc.
"""

from tpyc.modules import BuiltinModule, MethodDef, ParamDef, TypeParamKind
from tpyc.modules.helpers import make_binop_methods
from tpyc.typesys import (
    INT32, UINT64, BIGINT, FLOAT, FLOAT32, STR, STRING, STRVIEW, CHAR, VOID, BOOL, SELF,
    ALL_FIXED_INTS, FixedIntType,
    ListType, TypeParamRef, PtrType, NamedType, OwnType,
)

# Shorthand for type parameter T
T = TypeParamRef("T")

NAME = "tpy"


def init_module() -> BuiltinModule:
    """Initialize and return the tpy module."""
    module = BuiltinModule(NAME)

    # String: Explicit owned string type (std::string)
    module.register_type(STRING, cpp_type="std::string",
        extends=["NativeIterable[Char]", "Iterable[Char]", "Equatable"],
        methods={
        "__init__": [
            MethodDef(params=[], returns=STRING, cpp="std::string()"),
            MethodDef(params=[ParamDef("x", STR)], returns=STRING, cpp="std::string({0})"),
            MethodDef(params=[ParamDef("x", STRING)], returns=STRING, cpp="std::string({0})"),
            MethodDef(params=[ParamDef("x", STRVIEW)], returns=STRING, cpp="std::string({0})"),
            MethodDef(params=[ParamDef("x", BOOL)], returns=STRING, cpp="std::string(::tpy::bool_to_str({0}))"),
            MethodDef(params=[ParamDef("x", CHAR)], returns=STRING, cpp="std::string(::tpy::char_to_str({0}))"),
            *[MethodDef(params=[ParamDef("x", t)], returns=STRING,
                       cpp=f"::tpy::fixed_to_str<{t.to_cpp()}>({{0}})")
              for t in ALL_FIXED_INTS],
            MethodDef(params=[ParamDef("x", BIGINT)], returns=STRING, cpp="({0}).to_string()"),
            MethodDef(params=[ParamDef("x", FLOAT)], returns=STRING, cpp="::tpy::float_to_str({0})"),
            MethodDef(params=[ParamDef("x", FLOAT32)], returns=STRING, cpp="::tpy::float_to_str(static_cast<double>({0}))"),
        ],
        "__iter__": [MethodDef(
            params=[],
            returns=NamedType("Iterator", (CHAR,), is_protocol=True),
            cpp="::tpy::__iter__({self})",
            is_readonly=True, is_pure=True,
        )],
        "__len__": [MethodDef(
            params=[],
            returns=INT32,
            cpp="static_cast<int32_t>({self}.size())",
            is_readonly=True, is_pure=True,
        )],
        "__getitem__": [MethodDef(
            params=[ParamDef("index", INT32)],
            returns=CHAR,
            cpp="::tpy::__getitem__({self}, {0})",
            is_readonly=True, is_pure=True,
        )],
        "__add__": [
            MethodDef(
                params=[ParamDef("other", STR)],
                returns=STRING,
                cpp="::tpy::str_concat({self}, {0})",
            ),
            MethodDef(
                params=[ParamDef("other", STRING)],
                returns=STRING,
                cpp="::tpy::str_concat({self}, {0})",
            ),
            MethodDef(
                params=[ParamDef("other", STRVIEW)],
                returns=STRING,
                cpp="::tpy::str_concat({self}, {0})",
            ),
        ],
        "__mul__": [MethodDef(
            params=[ParamDef("n", INT32)],
            returns=STR, cpp="::tpy::str_repeat({self}, {0})", is_readonly=True, is_pure=True,
        )],
        "__rmul__": [MethodDef(
            params=[ParamDef("n", INT32)],
            returns=STR, cpp="::tpy::str_repeat({self}, {0})", is_readonly=True, is_pure=True,
        )],
        "split": [
            MethodDef(
                params=[],
                returns=OwnType(ListType(STR)),
                cpp="::tpy::str_split_whitespace({self})",
                is_readonly=True, is_pure=True,
            ),
            MethodDef(
                params=[ParamDef("sep", STR)],
                returns=OwnType(ListType(STR)),
                cpp="::tpy::str_split({self}, {0})",
                is_readonly=True, is_pure=True,
            ),
            MethodDef(
                params=[ParamDef("sep", STR), ParamDef("maxsplit", INT32)],
                returns=OwnType(ListType(STR)),
                cpp="::tpy::str_split({self}, {0}, {1})",
                is_readonly=True, is_pure=True,
            ),
        ],
        "join": [MethodDef(
            params=[ParamDef("items", NamedType("Iterable", (STR,), is_protocol=True))],
            returns=STR,
            cpp="::tpy::str_join({self}, {0})",
            is_readonly=True, is_pure=True,
        )],
        "strip": [MethodDef(
            params=[], returns=STRVIEW, cpp="::tpy::str_strip({self})", is_readonly=True, is_pure=True,
        )],
        "lstrip": [MethodDef(
            params=[], returns=STRVIEW, cpp="::tpy::str_lstrip({self})", is_readonly=True, is_pure=True,
        )],
        "rstrip": [MethodDef(
            params=[], returns=STRVIEW, cpp="::tpy::str_rstrip({self})", is_readonly=True, is_pure=True,
        )],
        "replace": [MethodDef(
            params=[ParamDef("old", STR), ParamDef("new", STR)],
            returns=STR, cpp="::tpy::str_replace({self}, {0}, {1})", is_readonly=True, is_pure=True,
        )],
        "find": [MethodDef(
            params=[ParamDef("sub", STR)],
            returns=INT32, cpp="::tpy::str_find({self}, {0})", is_readonly=True, is_pure=True,
        )],
        "rfind": [MethodDef(
            params=[ParamDef("sub", STR)],
            returns=INT32, cpp="::tpy::str_rfind({self}, {0})", is_readonly=True, is_pure=True,
        )],
        "index": [MethodDef(
            params=[ParamDef("sub", STR)],
            returns=INT32, cpp="::tpy::str_index({self}, {0})", is_readonly=True, is_pure=True,
        )],
        "startswith": [MethodDef(
            params=[ParamDef("prefix", STR)],
            returns=BOOL, cpp="::tpy::str_startswith({self}, {0})", is_readonly=True, is_pure=True,
        )],
        "endswith": [MethodDef(
            params=[ParamDef("suffix", STR)],
            returns=BOOL, cpp="::tpy::str_endswith({self}, {0})", is_readonly=True, is_pure=True,
        )],
        "upper": [MethodDef(
            params=[], returns=STR, cpp="::tpy::str_upper({self})", is_readonly=True, is_pure=True,
        )],
        "lower": [MethodDef(
            params=[], returns=STR, cpp="::tpy::str_lower({self})", is_readonly=True, is_pure=True,
        )],
        "count": [MethodDef(
            params=[ParamDef("sub", STR)],
            returns=INT32, cpp="::tpy::str_count({self}, {0})", is_readonly=True, is_pure=True,
        )],
        "isdigit": [MethodDef(
            params=[], returns=BOOL, cpp="::tpy::str_isdigit({self})", is_readonly=True, is_pure=True,
        )],
        "isalpha": [MethodDef(
            params=[], returns=BOOL, cpp="::tpy::str_isalpha({self})", is_readonly=True, is_pure=True,
        )],
        "isalnum": [MethodDef(
            params=[], returns=BOOL, cpp="::tpy::str_isalnum({self})", is_readonly=True, is_pure=True,
        )],
        "isspace": [MethodDef(
            params=[], returns=BOOL, cpp="::tpy::str_isspace({self})", is_readonly=True, is_pure=True,
        )],
        "isupper": [MethodDef(
            params=[], returns=BOOL, cpp="::tpy::str_isupper({self})", is_readonly=True, is_pure=True,
        )],
        "islower": [MethodDef(
            params=[], returns=BOOL, cpp="::tpy::str_islower({self})", is_readonly=True, is_pure=True,
        )],
        "capitalize": [MethodDef(
            params=[], returns=STR, cpp="::tpy::str_capitalize({self})", is_readonly=True, is_pure=True,
        )],
        "title": [MethodDef(
            params=[], returns=STR, cpp="::tpy::str_title({self})", is_readonly=True, is_pure=True,
        )],
        "swapcase": [MethodDef(
            params=[], returns=STR, cpp="::tpy::str_swapcase({self})", is_readonly=True, is_pure=True,
        )],
        "removeprefix": [MethodDef(
            params=[ParamDef("prefix", STR)],
            returns=STRVIEW, cpp="::tpy::str_removeprefix({self}, {0})", is_readonly=True, is_pure=True,
        )],
        "removesuffix": [MethodDef(
            params=[ParamDef("suffix", STR)],
            returns=STRVIEW, cpp="::tpy::str_removesuffix({self}, {0})", is_readonly=True, is_pure=True,
        )],
        "rindex": [MethodDef(
            params=[ParamDef("sub", STR)],
            returns=INT32, cpp="::tpy::str_rindex({self}, {0})", is_readonly=True, is_pure=True,
        )],
        "splitlines": [MethodDef(
            params=[], returns=OwnType(ListType(STR)),
            cpp="::tpy::str_splitlines({self})", is_readonly=True, is_pure=True,
        )],
        "__hash__": [MethodDef(params=[], returns=UINT64, cpp="::tpy::__hash__({self})", is_readonly=True, is_pure=True)],
    })

    # StrView: Explicit string view type (std::string_view)
    module.register_type(STRVIEW, cpp_type="std::string_view",
        extends=["NativeIterable[Char]", "Iterable[Char]", "Equatable"],
        methods={
        "__init__": [
            MethodDef(params=[], returns=STRVIEW, cpp='std::string_view()'),
            MethodDef(params=[ParamDef("x", STR)], returns=STRVIEW, cpp="std::string_view({0})"),
            MethodDef(params=[ParamDef("x", STRING)], returns=STRVIEW, cpp="std::string_view({0})"),
            MethodDef(params=[ParamDef("x", STRVIEW)], returns=STRVIEW, cpp="{0}"),
        ],
        "__iter__": [MethodDef(
            params=[],
            returns=NamedType("Iterator", (CHAR,), is_protocol=True),
            cpp="::tpy::__iter__({self})",
            is_readonly=True, is_pure=True,
        )],
        "__len__": [MethodDef(
            params=[],
            returns=INT32,
            cpp="static_cast<int32_t>({self}.size())",
            is_readonly=True, is_pure=True,
        )],
        "__getitem__": [MethodDef(
            params=[ParamDef("index", INT32)],
            returns=CHAR,
            cpp="::tpy::__getitem__({self}, {0})",
            is_readonly=True, is_pure=True,
        )],
        "__add__": [
            MethodDef(
                params=[ParamDef("other", STR)],
                returns=STRING,
                cpp="::tpy::str_concat({self}, {0})",
            ),
            MethodDef(
                params=[ParamDef("other", STRING)],
                returns=STRING,
                cpp="::tpy::str_concat({self}, {0})",
            ),
            MethodDef(
                params=[ParamDef("other", STRVIEW)],
                returns=STRING,
                cpp="::tpy::str_concat({self}, {0})",
            ),
        ],
        "__mul__": [MethodDef(
            params=[ParamDef("n", INT32)],
            returns=STR, cpp="::tpy::str_repeat({self}, {0})", is_readonly=True, is_pure=True,
        )],
        "__rmul__": [MethodDef(
            params=[ParamDef("n", INT32)],
            returns=STR, cpp="::tpy::str_repeat({self}, {0})", is_readonly=True, is_pure=True,
        )],
        "split": [
            MethodDef(
                params=[],
                returns=OwnType(ListType(STR)),
                cpp="::tpy::str_split_whitespace({self})",
                is_readonly=True, is_pure=True,
            ),
            MethodDef(
                params=[ParamDef("sep", STR)],
                returns=OwnType(ListType(STR)),
                cpp="::tpy::str_split({self}, {0})",
                is_readonly=True, is_pure=True,
            ),
            MethodDef(
                params=[ParamDef("sep", STR), ParamDef("maxsplit", INT32)],
                returns=OwnType(ListType(STR)),
                cpp="::tpy::str_split({self}, {0}, {1})",
                is_readonly=True, is_pure=True,
            ),
        ],
        "join": [MethodDef(
            params=[ParamDef("items", NamedType("Iterable", (STR,), is_protocol=True))],
            returns=STR,
            cpp="::tpy::str_join({self}, {0})",
            is_readonly=True, is_pure=True,
        )],
        "strip": [MethodDef(
            params=[], returns=STRVIEW, cpp="::tpy::str_strip({self})", is_readonly=True, is_pure=True,
        )],
        "lstrip": [MethodDef(
            params=[], returns=STRVIEW, cpp="::tpy::str_lstrip({self})", is_readonly=True, is_pure=True,
        )],
        "rstrip": [MethodDef(
            params=[], returns=STRVIEW, cpp="::tpy::str_rstrip({self})", is_readonly=True, is_pure=True,
        )],
        "replace": [MethodDef(
            params=[ParamDef("old", STR), ParamDef("new", STR)],
            returns=STR, cpp="::tpy::str_replace({self}, {0}, {1})", is_readonly=True, is_pure=True,
        )],
        "find": [MethodDef(
            params=[ParamDef("sub", STR)],
            returns=INT32, cpp="::tpy::str_find({self}, {0})", is_readonly=True, is_pure=True,
        )],
        "rfind": [MethodDef(
            params=[ParamDef("sub", STR)],
            returns=INT32, cpp="::tpy::str_rfind({self}, {0})", is_readonly=True, is_pure=True,
        )],
        "index": [MethodDef(
            params=[ParamDef("sub", STR)],
            returns=INT32, cpp="::tpy::str_index({self}, {0})", is_readonly=True, is_pure=True,
        )],
        "startswith": [MethodDef(
            params=[ParamDef("prefix", STR)],
            returns=BOOL, cpp="::tpy::str_startswith({self}, {0})", is_readonly=True, is_pure=True,
        )],
        "endswith": [MethodDef(
            params=[ParamDef("suffix", STR)],
            returns=BOOL, cpp="::tpy::str_endswith({self}, {0})", is_readonly=True, is_pure=True,
        )],
        "upper": [MethodDef(
            params=[], returns=STR, cpp="::tpy::str_upper({self})", is_readonly=True, is_pure=True,
        )],
        "lower": [MethodDef(
            params=[], returns=STR, cpp="::tpy::str_lower({self})", is_readonly=True, is_pure=True,
        )],
        "count": [MethodDef(
            params=[ParamDef("sub", STR)],
            returns=INT32, cpp="::tpy::str_count({self}, {0})", is_readonly=True, is_pure=True,
        )],
        "isdigit": [MethodDef(
            params=[], returns=BOOL, cpp="::tpy::str_isdigit({self})", is_readonly=True, is_pure=True,
        )],
        "isalpha": [MethodDef(
            params=[], returns=BOOL, cpp="::tpy::str_isalpha({self})", is_readonly=True, is_pure=True,
        )],
        "isalnum": [MethodDef(
            params=[], returns=BOOL, cpp="::tpy::str_isalnum({self})", is_readonly=True, is_pure=True,
        )],
        "isspace": [MethodDef(
            params=[], returns=BOOL, cpp="::tpy::str_isspace({self})", is_readonly=True, is_pure=True,
        )],
        "isupper": [MethodDef(
            params=[], returns=BOOL, cpp="::tpy::str_isupper({self})", is_readonly=True, is_pure=True,
        )],
        "islower": [MethodDef(
            params=[], returns=BOOL, cpp="::tpy::str_islower({self})", is_readonly=True, is_pure=True,
        )],
        "capitalize": [MethodDef(
            params=[], returns=STR, cpp="::tpy::str_capitalize({self})", is_readonly=True, is_pure=True,
        )],
        "title": [MethodDef(
            params=[], returns=STR, cpp="::tpy::str_title({self})", is_readonly=True, is_pure=True,
        )],
        "swapcase": [MethodDef(
            params=[], returns=STR, cpp="::tpy::str_swapcase({self})", is_readonly=True, is_pure=True,
        )],
        "removeprefix": [MethodDef(
            params=[ParamDef("prefix", STR)],
            returns=STRVIEW, cpp="::tpy::str_removeprefix({self}, {0})", is_readonly=True, is_pure=True,
        )],
        "removesuffix": [MethodDef(
            params=[ParamDef("suffix", STR)],
            returns=STRVIEW, cpp="::tpy::str_removesuffix({self}, {0})", is_readonly=True, is_pure=True,
        )],
        "rindex": [MethodDef(
            params=[ParamDef("sub", STR)],
            returns=INT32, cpp="::tpy::str_rindex({self}, {0})", is_readonly=True, is_pure=True,
        )],
        "splitlines": [MethodDef(
            params=[], returns=OwnType(ListType(STR)),
            cpp="::tpy::str_splitlines({self})", is_readonly=True, is_pure=True,
        )],
        "__hash__": [MethodDef(params=[], returns=UINT64, cpp="::tpy::__hash__({self})", is_readonly=True, is_pure=True)],
    })

    # copy() - explicit copy for ownership transfer
    module.function("copy", overloads=[
        MethodDef(
            params=[ParamDef("x", T)],
            returns=OwnType(T),
            cpp="{0}",
        ),
    ], special_handling=True)

    # try_parse(EnumType, str) -> Optional[EnumType]
    module.function("try_parse", overloads=[], special_handling=True)



    return module
