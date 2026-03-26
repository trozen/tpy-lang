"""
Dump documentation for builtin types in markdown format.
"""

from .modules import MethodDef


def _format_type(t) -> str:
    """Format a type for display (handles TpyType and string type params)."""
    if isinstance(t, str):
        return t
    return str(t)


def _format_annotations(overload: MethodDef) -> str:
    """Format method/function annotations for signature display."""
    annotations: list[str] = []
    if overload.is_noalloc:
        annotations.append("@noalloc")
    if overload.is_pure:
        annotations.append("@pure")
    elif overload.is_readonly:
        annotations.append("@readonly")
    return " ".join(annotations)


def _format_signature(name: str, overload: MethodDef) -> str:
    """Format a callable signature with inline annotations."""
    params = ", ".join(f"{p.name}: {_format_type(p.type)}" for p in overload.params)
    ret = _format_type(overload.returns)
    annotations = _format_annotations(overload)
    if annotations:
        return f"{annotations} {name}({params}) -> {ret}"
    return f"{name}({params}) -> {ret}"


def _print_signature_item(name: str, overload: MethodDef) -> None:
    """Print one callable entry."""
    print(f"- `{_format_signature(name, overload)}`")


def dump_builtin_types() -> None:
    """Dump documentation for all builtin types in markdown format.

    Note: builtin types and functions are now fully defined in .py stubs
    under lib/tpy/. This function is a no-op placeholder.
    """
    print("# TurboPython Builtin Types\n")
    print("All types are defined in .py stubs under lib/tpy/.\n")


def _print_type_doc(qname: str, type_def) -> None:
    """Print documentation for a type."""
    if type_def.type_obj is not None:
        name = str(type_def.type_obj)
    else:
        name = qname.split(".")[-1]
    if type_def.type_params:
        name += f"[{', '.join(type_def.type_params)}]"
    print(f"### {name}\n")

    if type_def.extends:
        print(f"Extends: {', '.join(type_def.extends)}\n")

    init_overloads = type_def.methods.get("__init__")
    if init_overloads:
        print("**Constructors:**\n")
        base_name = qname.split(".")[-1]
        for ctor in init_overloads:
            _print_signature_item(base_name, ctor)
        print()

    if type_def.methods:
        print("**Methods:**\n")
        for method_name, overloads in type_def.methods.items():
            if method_name == "__init__":
                continue
            for ovl in overloads:
                _print_signature_item(method_name, ovl)
        print()


def _print_function_doc(name: str, func_def) -> None:
    """Print documentation for a function."""
    print(f"### {name}()\n")
    for ovl in func_def.overloads:
        _print_signature_item(name, ovl)
    print()


def _print_protocol_doc(name: str, proto_def) -> None:
    """Print documentation for a protocol."""
    if proto_def.type_params:
        name += f"[{', '.join(proto_def.type_params)}]"
    print(f"### {name} (protocol)\n")

    if proto_def.methods:
        print("**Required methods:**\n")
        for method_name, method in proto_def.methods.items():
            _print_signature_item(method_name, method)
        print()
    else:
        print("Marker protocol (no required methods)\n")
