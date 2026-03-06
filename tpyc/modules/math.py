"""
TurboPython math module.

Provides mathematical functions matching Python's math module.
"""

from tpyc.modules import BuiltinModule, MethodDef, ParamDef
from tpyc.typesys import FLOAT, BIGINT

NAME = "math"


def init_module() -> BuiltinModule:
    """Initialize and return the math module."""
    module = BuiltinModule(NAME)

    # math.log(x) - natural logarithm
    # math.log(x, base) - logarithm with specified base
    module.function("log", overloads=[
        # Single argument: natural log
        MethodDef(
            params=[ParamDef("x", FLOAT)],
            returns=FLOAT,
            cpp="std::log({0})",
        ),
        # Two arguments: log with base
        MethodDef(
            params=[ParamDef("x", FLOAT), ParamDef("base", FLOAT)],
            returns=FLOAT,
            cpp="(std::log({0}) / std::log({1}))",
        ),
    ])

    # math.log10(x) - base-10 logarithm
    module.function("log10", overloads=[
        MethodDef(
            params=[ParamDef("x", FLOAT)],
            returns=FLOAT,
            cpp="std::log10({0})",
        ),
    ])

    # math.log2(x) - base-2 logarithm
    module.function("log2", overloads=[
        MethodDef(
            params=[ParamDef("x", FLOAT)],
            returns=FLOAT,
            cpp="std::log2({0})",
        ),
    ])

    # math.sqrt(x) - square root
    module.function("sqrt", overloads=[
        MethodDef(
            params=[ParamDef("x", FLOAT)],
            returns=FLOAT,
            cpp="std::sqrt({0})",
        ),
    ])

    # math.pow(x, y) - x raised to the power y
    module.function("pow", overloads=[
        MethodDef(
            params=[ParamDef("x", FLOAT), ParamDef("y", FLOAT)],
            returns=FLOAT,
            cpp="std::pow({0}, {1})",
        ),
    ])

    # math.exp(x) - e raised to the power x
    module.function("exp", overloads=[
        MethodDef(
            params=[ParamDef("x", FLOAT)],
            returns=FLOAT,
            cpp="std::exp({0})",
        ),
    ])

    # math.floor(x) - largest integer <= x (Python 3 returns int)
    module.function("floor", overloads=[
        MethodDef(
            params=[ParamDef("x", FLOAT)],
            returns=BIGINT,
            cpp="BigInt::from_float(std::floor({0}))",
        ),
    ])

    # math.ceil(x) - smallest integer >= x (Python 3 returns int)
    module.function("ceil", overloads=[
        MethodDef(
            params=[ParamDef("x", FLOAT)],
            returns=BIGINT,
            cpp="BigInt::from_float(std::ceil({0}))",
        ),
    ])

    # math.sin(x) - sine
    module.function("sin", overloads=[
        MethodDef(
            params=[ParamDef("x", FLOAT)],
            returns=FLOAT,
            cpp="std::sin({0})",
        ),
    ])

    # math.cos(x) - cosine
    module.function("cos", overloads=[
        MethodDef(
            params=[ParamDef("x", FLOAT)],
            returns=FLOAT,
            cpp="std::cos({0})",
        ),
    ])

    # math.tan(x) - tangent
    module.function("tan", overloads=[
        MethodDef(
            params=[ParamDef("x", FLOAT)],
            returns=FLOAT,
            cpp="std::tan({0})",
        ),
    ])

    # math.fabs(x) - absolute value (float)
    module.function("fabs", overloads=[
        MethodDef(
            params=[ParamDef("x", FLOAT)],
            returns=FLOAT,
            cpp="std::fabs({0})",
        ),
    ])

    return module
