# tpy: macro_module
"""struct module as compile-time macros.

Parses format string literals at compile time and expands to typed
reads via tpy.unsafe.unsafe_read_* functions.

Supported functions: unpack_from, unpack, calcsize.
Supported format codes: b, B, h, H, i, I, q, Q, f, d, ?, Ns, x.
Supported byte orders: < (little-endian), > ! (big-endian), = @ (native).
Format string must be a literal (compile-time expansion).

TODO: pack, pack_into (need statement expression or buffer-builder pattern).
TODO: e (float16), P (pointer), n/N (ssize_t/size_t).
"""

from tpyc.macro_api import (
    CallMacroContext, MacroArg, MacroError,
    call_macro, macro_deps, ast, Expr,
    TpyStrLiteral, TpyIntLiteral,
)

macro_deps("tpy.unsafe")

# Format code -> (byte_size, read_function_name)
_FORMAT_CODES: dict[str, tuple[int, str]] = {
    'b': (1, 'unsafe_read_i8'),
    'B': (1, 'unsafe_read_u8'),
    'h': (2, 'unsafe_read_i16'),
    'H': (2, 'unsafe_read_u16'),
    'i': (4, 'unsafe_read_i32'),
    'I': (4, 'unsafe_read_u32'),
    'q': (8, 'unsafe_read_i64'),
    'Q': (8, 'unsafe_read_u64'),
    'f': (4, 'unsafe_read_f32'),
    'd': (8, 'unsafe_read_f64'),
    '?': (1, 'unsafe_read_u8'),  # bool: read as u8, wrap in bool()
}


def _parse_format(fmt: str) -> tuple[str, list[tuple[str, int, bool]]]:
    """Parse a struct format string.

    Returns (byte_order, fields) where fields is a list of
    (read_func_name, byte_size, is_bool) tuples.
    For 'Ns' format, returns ('unsafe_read_bytes', N, False).
    """
    if not fmt:
        raise MacroError("Empty format string")

    # Parse byte order prefix
    byte_order = '<'  # default: little-endian
    i = 0
    if fmt[0] in '<>!=@':
        byte_order = fmt[0]
        i = 1

    fields: list[tuple[str, int, bool]] = []
    while i < len(fmt):
        # Check for count prefix (e.g. '8s', '3B')
        count = 0
        while i < len(fmt) and fmt[i].isdigit():
            count = count * 10 + int(fmt[i])
            i += 1

        if i >= len(fmt):
            raise MacroError(f"Incomplete format string: '{fmt}'")

        code = fmt[i]
        i += 1

        if code == 's':
            if count == 0:
                raise MacroError("'s' format requires a count prefix (e.g. '8s')")
            fields.append(('unsafe_read_bytes', count, False))
        elif code in _FORMAT_CODES:
            byte_size, func_name = _FORMAT_CODES[code]
            is_bool = (code == '?')
            repeat = count if count > 0 else 1
            for _ in range(repeat):
                fields.append((func_name, byte_size, is_bool))
        elif code == 'x':
            # Padding byte -- skip
            repeat = count if count > 0 else 1
            fields.append(('__pad__', repeat, False))
        else:
            raise MacroError(f"Unsupported struct format code: '{code}'")

    return byte_order, fields


def _needs_bswap(byte_order: str) -> bool:
    """Check if byte order requires byte swapping (big-endian on LE host)."""
    return byte_order in ('>', '!')


@call_macro
def unpack_from(
    ctx: CallMacroContext,
    fmt_arg: MacroArg,
    data_arg: MacroArg,
    offset_arg: MacroArg | None = None,
) -> Expr:
    """Expand struct.unpack_from('<hh', data, offset) to typed tuple."""
    if not isinstance(fmt_arg.expr, TpyStrLiteral):
        raise MacroError(
            "struct.unpack_from: format string must be a string literal")

    fmt = fmt_arg.expr.value
    byte_order, fields = _parse_format(fmt)

    if _needs_bswap(byte_order):
        raise MacroError(
            "struct.unpack_from: big-endian byte order not yet implemented")

    # Base offset expression (default 0)
    base_offset = offset_arg.expr if offset_arg is not None else ast.int_lit(0)

    elements: list[Expr] = []
    byte_pos = 0
    for func_name, size, is_bool in fields:
        if func_name == '__pad__':
            byte_pos += size
            continue

        # Build offset: base_offset + byte_pos (constant-fold when possible)
        if byte_pos == 0:
            off_expr = base_offset
        elif isinstance(base_offset, TpyIntLiteral):
            off_expr = ast.int_lit(base_offset.value + byte_pos)
        else:
            off_expr = ast.binop(base_offset, "+", ast.int_lit(byte_pos))

        _unsafe = ast.name("tpy.unsafe")
        if func_name == 'unsafe_read_bytes':
            call: Expr = ast.method_call(_unsafe, func_name, [data_arg.expr, off_expr, ast.int_lit(size)])
        else:
            call = ast.method_call(_unsafe, func_name, [data_arg.expr, off_expr])

        if is_bool:
            call = ast.call("bool", [ast.binop(call, "!=", ast.int_lit(0))])

        elements.append(call)
        byte_pos += size

    if not elements:
        raise MacroError("struct.unpack_from: format string has no data fields")

    return ast.tuple_lit(elements)


@call_macro
def unpack(
    ctx: CallMacroContext,
    fmt_arg: MacroArg,
    data_arg: MacroArg,
) -> Expr:
    """Expand struct.unpack('<hh', data) -- same as unpack_from with offset 0."""
    return unpack_from(ctx, fmt_arg, data_arg)


@call_macro
def calcsize(
    ctx: CallMacroContext,
    fmt_arg: MacroArg,
) -> Expr:
    """Expand struct.calcsize('<hh') to a compile-time integer literal."""
    if not isinstance(fmt_arg.expr, TpyStrLiteral):
        raise MacroError(
            "struct.calcsize: format string must be a string literal")

    fmt = fmt_arg.expr.value
    _, fields = _parse_format(fmt)

    total = 0
    for _, size, _ in fields:
        total += size

    return ast.int_lit(total)
