"""
TurboPython built-in functions (Python builtins).

Special-handling builtins that can't be expressed as .py stubs.
Most builtins are defined in lib/tpy/tpy/_builtins/.
"""

from tpyc.modules.defs import BuiltinModule, MethodDef, ParamDef
from tpyc.typesys import VOID, SLICE

NAME = "builtins"

def init_module() -> BuiltinModule:
    """Initialize and return the builtins module."""
    module = BuiltinModule(NAME)

    # None: Void type (used for function returns)
    module.register_type(VOID, cpp_type="void", methods={})

    # slice: built-in type for subscript ranges (start/stop are Optional[Int32])
    module.register_type(SLICE, cpp_type="::tpy::Slice", methods={})

    # print() - variadic print function
    # Special handling in sema/ and codegen_cpp/ (variadic, polymorphic)
    module.function("print", overloads=[], special_handling=True)

    # isinstance() - type checking for union type narrowing
    # Special handling in sema (validates union member) and codegen (std::holds_alternative)
    module.function("isinstance", overloads=[], special_handling=True)

    return module
