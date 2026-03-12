# Regression: str-param or ternary/or of str-params initialized into a local that
# later gets promoted to std::string (e.g. via +=) must be wrapped std::string(...).
# The str param is std::string_view at C++ runtime; its explicit constructor requires
# an explicit conversion when assigning to a std::string variable.

def from_param(a: str) -> None:
    # a is std::string_view at runtime; x gets promoted to str due to +=
    x = a  # tpyc: type(str)
    x += "!"
    print(x)


def from_param_return(a: str) -> str:
    # return a or b where both are str params: result is string_view at runtime
    x = a or "default"  # tpyc: type(str)
    x += "."
    return x


def from_or_params(a: str, b: str) -> None:
    # a or b is string_view at runtime; x promoted to str via +=
    x = a or b  # tpyc: type(str)
    x += "!"
    print(x)


def from_ternary_params(a: str, b: str, cond: bool) -> None:
    # ternary of str params is string_view at runtime; x promoted to str via +=
    x = a if cond else b  # tpyc: type(str)
    x += "!"
    print(x)


def return_or_params(a: str, b: str) -> str:
    # return a or b: both are string_view at runtime, result must be owned string
    return a or b


def return_ternary_params(a: str, b: str, cond: bool) -> str:
    # return ternary of str params: result must be owned string
    return a if cond else b


from_param("hello")
print(from_param_return("hi"))
from_or_params("", "world")
from_or_params("hello", "world")
from_ternary_params("hello", "world", True)
from_ternary_params("hello", "world", False)
print(return_or_params("", "fallback"))
print(return_or_params("first", "second"))
print(return_ternary_params("yes", "no", True))
print(return_ternary_params("yes", "no", False))
