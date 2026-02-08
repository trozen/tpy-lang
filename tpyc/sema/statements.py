"""
TurboPython Statement Analysis

Statement analysis including variable declarations, assignments, and control flow.
"""

from __future__ import annotations
from typing import TYPE_CHECKING

from ..typesys import (
    TpyType, Int32Type, BigIntType, IntLiteralType, FloatType, OwnType,
    ListType, PendingListType, NamedType, CharType, StrType, TypeParamRef,
    ListLiteralInfo, ConstPtrType, INT32, VOID, BIGINT, is_protocol_type,
)
from ..parse import (
    TpyStmt, TpyVarDecl, TpyAssign, TpyAugAssign, TpyExprStmt, TpyReturn,
    TpyIf, TpyWhile, TpyFor, TpyForEach, TpyBreak, TpyContinue,
    TpyCall, TpyArrayLiteral, TpySubscript, TpyStrLiteral, TpyName,
    TpyFieldAccess, TpyFunction,
)
from ..namespace import Namespace
from ..coercions import CoercionContext
from .diagnostics import SemanticError, Scope

if TYPE_CHECKING:
    from .context import SemanticContext
    from .type_ops import TypeOperations
    from .compatibility import TypeCompatibility
    from .list_literals import ListLiteralTracker
    from .expressions import ExpressionAnalyzer
    from .protocols import ProtocolChecker

from tpyc import modules as builtin_modules


class StatementAnalyzer:
    """Statement analysis."""

    def __init__(
        self,
        ctx: SemanticContext,
        type_ops: TypeOperations,
        compat: TypeCompatibility,
        list_tracker: ListLiteralTracker,
        protocols: ProtocolChecker,
    ):
        self.ctx = ctx
        self.type_ops = type_ops
        self.compat = compat
        self.list_tracker = list_tracker
        self.protocols = protocols
        # Set via set_cross_deps() to break circular dependency
        self.expr: ExpressionAnalyzer | None = None

    def set_cross_deps(self, expr: ExpressionAnalyzer) -> None:
        """Wire circular dependencies (must be called before analyze_stmt)."""
        self.expr = expr

    def analyze_stmt(self, stmt: TpyStmt) -> None:
        """Analyze a statement."""
        if isinstance(stmt, TpyVarDecl):
            self._analyze_var_decl(stmt)
        elif isinstance(stmt, TpyAssign):
            self._analyze_assign(stmt)
        elif isinstance(stmt, TpyAugAssign):
            self._analyze_aug_assign(stmt)
        elif isinstance(stmt, TpyExprStmt):
            self.expr.analyze_expr(stmt.expr)
        elif isinstance(stmt, TpyReturn):
            if stmt.value:
                ret_type = self.expr.analyze_expr(stmt.value)
                expected = self.ctx.current_function.return_type if self.ctx.current_function else VOID
                stmt.value = self.compat.coerce_expr(stmt.value, ret_type, expected, "return value",
                                                      coercion_ctx=CoercionContext.RETURN, is_return=True)
                # Check for lvalue returned as Own[T] without explicit copy()
                if isinstance(expected, OwnType) and self.compat.is_lvalue(stmt.value):
                    if not self.compat.is_copy_call(stmt.value):
                        raise self.ctx.error(
                            f"Cannot return lvalue as Own[{expected.wrapped}] without explicit copy(). "
                            f"Use 'return copy(...)' instead.",
                            stmt.value
                        )
                # Check for dangling reference (returning local/temporary as reference)
                self.compat.check_dangling_reference(stmt.value, expected, stmt.loc)
        elif isinstance(stmt, TpyIf):
            self.expr.analyze_expr(stmt.condition)
            for s in stmt.then_body:
                self.analyze_stmt(s)
            for s in stmt.else_body:
                self.analyze_stmt(s)
        elif isinstance(stmt, TpyWhile):
            self.expr.analyze_expr(stmt.condition)
            self.ctx.loop_depth += 1
            for s in stmt.body:
                self.analyze_stmt(s)
            self.ctx.loop_depth -= 1
        elif isinstance(stmt, TpyFor):
            self.expr.analyze_expr(stmt.start)
            self.expr.analyze_expr(stmt.end)
            inner_scope = Scope(self.ctx.current_scope)
            inner_scope.define(stmt.var, INT32)
            old_scope = self.ctx.current_scope
            self.ctx.current_scope = inner_scope
            # Set up inner namespace for loop variable
            old_ns = self.ctx.current_ns
            if self.ctx.current_ns:
                inner_ns = Namespace(parent=self.ctx.current_ns)
                inner_ns.bind_variable(stmt.var, INT32)
                self.ctx.current_ns = inner_ns
            self.ctx.loop_depth += 1
            for s in stmt.body:
                self.analyze_stmt(s)
            self.ctx.loop_depth -= 1
            self.ctx.current_scope = old_scope
            self.ctx.current_ns = old_ns
        elif isinstance(stmt, TpyForEach):
            iterable_type = self.expr.analyze_expr(stmt.iterable)
            elem_type = self.list_tracker.get_iterable_element_type(iterable_type)
            inner_scope = Scope(self.ctx.current_scope)
            inner_scope.define(stmt.var, elem_type)
            old_scope = self.ctx.current_scope
            self.ctx.current_scope = inner_scope
            # Set up inner namespace for loop variable
            old_ns = self.ctx.current_ns
            if self.ctx.current_ns:
                inner_ns = Namespace(parent=self.ctx.current_ns)
                inner_ns.bind_variable(stmt.var, elem_type)
                self.ctx.current_ns = inner_ns
            # Track loop variable for reassignment checks
            self.ctx.loop_vars.add(stmt.var)
            self.ctx.loop_depth += 1
            for s in stmt.body:
                self.analyze_stmt(s)
            self.ctx.loop_depth -= 1
            self.ctx.loop_vars.discard(stmt.var)
            self.ctx.current_scope = old_scope
            self.ctx.current_ns = old_ns
        elif isinstance(stmt, TpyBreak):
            if self.ctx.loop_depth == 0:
                raise SemanticError("'break' outside loop")
        elif isinstance(stmt, TpyContinue):
            if self.ctx.loop_depth == 0:
                raise SemanticError("'continue' outside loop")

    def _check_nonvalue_rebinding(self, name: str, node: TpyStmt) -> None:
        """Error if reassigning a non-value-type param or loop variable."""
        existing_type = self.ctx.current_scope.lookup(name)
        if existing_type is None or existing_type.is_value_type():
            return
        # Check function parameters
        func = self.ctx.current_function
        if isinstance(func, TpyFunction):
            for pname, ptype in func.params:
                if pname == name and not ptype.is_value_type():
                    raise self.ctx.error(
                        f"Cannot reassign parameter '{name}' of type '{ptype}'; "
                        f"assign to a new local variable instead",
                        node
                    )
        # Check for-each loop variables
        if name in self.ctx.loop_vars:
            raise self.ctx.error(
                f"Cannot reassign loop variable '{name}' of type '{existing_type}'; "
                f"assign to a new local variable instead",
                node
            )

    def _analyze_var_decl(self, stmt: TpyVarDecl) -> None:
        """Analyze a variable declaration."""
        # Validate the type annotation if present
        # Allow TypeParamRef when inside a generic record's methods
        if stmt.type:
            try:
                self.type_ops.validate_type(stmt.type, allow_type_param_ref=bool(self.ctx.record_ctx.type_params))
            except SemanticError as e:
                raise self.ctx.error(str(e), stmt)

        # Own[T] is only valid for function parameters and return types, not variables
        if stmt.type and isinstance(stmt.type, OwnType):
            raise self.ctx.error(
                f"Own[{stmt.type.wrapped}] cannot be used as a variable type. "
                f"Use '{stmt.type.wrapped}' instead (Own[T] is for parameters and return types only)",
                stmt
            )

        # Protocol types can only be used for function parameters, not variables
        if stmt.type and is_protocol_type(stmt.type):
            raise self.ctx.error(
                f"Protocol type '{stmt.type.name}' cannot be used as a variable type. "
                f"Protocols are only valid for function parameters",
                stmt
            )

        # Check if this is a reassignment (variable already exists in scope)
        existing_type = self.ctx.current_scope.lookup(stmt.name)

        # Disallow reassignment of non-value-type params and loop vars
        if existing_type is not None:
            self._check_nonvalue_rebinding(stmt.name, stmt)

        if stmt.init:
            # Handle empty list literal or generic type constructor with explicit type annotation
            # Note: [] * N is collapsed to [] in the parser
            is_empty_literal = isinstance(stmt.init, TpyArrayLiteral) and not stmt.init.elements
            is_generic_constructor = (isinstance(stmt.init, TpyCall) and
                                      not stmt.init.args and
                                      stmt.init.call_type is None and
                                      builtin_modules.lookup_generic_type(stmt.init.func) is not None)

            if (is_empty_literal or is_generic_constructor) and stmt.type:
                # Check if annotation matches the constructor's generic type
                annotation_matches = False
                if is_generic_constructor:
                    lookup = builtin_modules.lookup_generic_type(stmt.init.func)
                    annotation_matches = (lookup is not None and
                                          stmt.type.qualified_name() == lookup.qualified_name)
                else:
                    # Empty literal [] can match list[T] annotation
                    annotation_matches = isinstance(stmt.type, ListType)

                if annotation_matches:
                    if isinstance(stmt.type, ListType):
                        # list[T]: Use PendingListType for potential Array optimization
                        elem_type = stmt.type.element_type
                        # Set call_type so codegen generates explicit type (e.g., std::vector<int>())
                        if is_generic_constructor:
                            stmt.init.call_type = stmt.type  # type: ignore
                        if self.ctx.current_function is None:
                            # Global context: return ListType directly
                            init_type = ListType(elem_type)
                        else:
                            # Function-local context: create PendingListType
                            literal_id = self.ctx.literal_counter
                            self.ctx.literal_counter += 1
                            info = ListLiteralInfo(
                                literal_id=literal_id,
                                expr=stmt.init,
                                element_type=elem_type,
                                size=0,
                                is_global=self.ctx.is_top_level,
                                has_explicit_annotation=True,
                                explicit_type=stmt.type
                            )
                            self.ctx.list_literals[literal_id] = info
                            self.ctx.pending_resolutions.append(literal_id)
                            init_type = PendingListType(elem_type, 0, literal_id)
                    else:
                        # Other generic types (Array, etc.): use annotation directly
                        init_type = stmt.type
                        # Set call_type so codegen knows the concrete template type
                        if is_generic_constructor:
                            stmt.init.call_type = stmt.type  # type: ignore
                    # Cache expr_type since we bypassed _analyze_expr
                    self.ctx.set_expr_type(stmt.init, init_type)
                else:
                    func_name = stmt.init.func if is_generic_constructor else "[]"
                    raise SemanticError(
                        f"{func_name} requires matching type annotation, got {stmt.type}"
                    )
            else:
                init_type = self.expr.analyze_expr(stmt.init)

            # Track list literal to variable mapping for mutation detection
            if isinstance(init_type, PendingListType):
                literal_id = init_type.literal_id
                self.ctx.variable_to_literal[stmt.name] = literal_id
                info = self.ctx.list_literals[literal_id]
                info.variable_name = stmt.name

                # If explicit annotation is provided, record it
                if stmt.type and isinstance(stmt.init, TpyArrayLiteral):
                    info.has_explicit_annotation = True
                    info.explicit_type = stmt.type

            if stmt.type:
                # Special case: single-char string literal can be assigned to Char
                if (isinstance(stmt.type, CharType) and isinstance(init_type, StrType) and
                    isinstance(stmt.init, TpyStrLiteral) and len(stmt.init.value) == 1):
                    pass  # Allow str literal -> Char
                else:
                    stmt.init = self.compat.coerce_expr(stmt.init, init_type, stmt.type,
                                                         f"variable '{stmt.name}'",
                                                         coercion_ctx=CoercionContext.INIT)
                var_type = stmt.type
            elif existing_type:
                # Reassignment: check if we need to upgrade IntLiteralType
                if isinstance(existing_type, IntLiteralType) and isinstance(init_type, (Int32Type, BigIntType)):
                    # Upgrade from IntLiteralType to concrete type
                    var_type = init_type
                    # Update var_types so codegen knows the resolved type
                    orig_decl = self.ctx.var_decl_by_name.get(stmt.name)
                    if orig_decl:
                        self.ctx.var_types[id(orig_decl)] = init_type
                else:
                    # Normal reassignment: use existing type, check compatibility
                    stmt.init = self.compat.coerce_expr(stmt.init, init_type, existing_type,
                                                         f"reassignment to '{stmt.name}'",
                                                         coercion_ctx=CoercionContext.ASSIGN)
                    var_type = existing_type
            else:
                # New variable: resolve IntLiteralType to BigInt (Python int semantics)
                # This ensures Int32 + untyped_var promotes to BigInt correctly
                if isinstance(init_type, IntLiteralType):
                    var_type = BIGINT
                # Unwrap OwnType - Own[T] indicates ownership transfer, not variable type
                elif isinstance(init_type, OwnType):
                    var_type = init_type.wrapped
                else:
                    var_type = init_type
        elif stmt.type:
            if not stmt.type.is_value_type():
                raise self.ctx.error(
                    f"Variable '{stmt.name}' of type '{stmt.type}' must have an initializer",
                    stmt
                )
            var_type = stmt.type
        else:
            raise SemanticError(f"Variable '{stmt.name}' has no type annotation and no initializer")

        self.ctx.current_scope.define(stmt.name, var_type)
        if self.ctx.current_ns:
            self.ctx.current_ns.bind_variable(stmt.name, var_type)
        # Track top-level declarations with line number for order-aware codegen
        # Use earliest declaration line (min) so uses between redeclarations work
        if self.ctx.is_top_level:
            decl_line = stmt.loc.line if stmt.loc else 0
            if stmt.name not in self.ctx.top_level_decls:
                self.ctx.top_level_decls[stmt.name] = decl_line
            else:
                self.ctx.top_level_decls[stmt.name] = min(self.ctx.top_level_decls[stmt.name], decl_line)
        # Track var_decl for later type updates
        if isinstance(var_type, IntLiteralType):
            self.ctx.var_decl_by_name[stmt.name] = stmt

    def _analyze_assign(self, stmt: TpyAssign) -> None:
        """Analyze an assignment."""
        target_type = self.expr.analyze_expr(stmt.target)
        value_type = self.expr.analyze_expr(stmt.value)

        # Disallow reassignment of non-value-type params and loop vars
        if isinstance(stmt.target, TpyName):
            self._check_nonvalue_rebinding(stmt.target.name, stmt)

        # Prevent assignment to read-only types via MutableSequence protocol check
        if isinstance(stmt.target, TpySubscript):
            obj_type = self.ctx.get_expr_type(stmt.target.obj)
            elem_type = obj_type.get_element_type()
            if elem_type is not None:
                # Check if type conforms to MutableSequence[elem_type]
                mutable_seq = NamedType("MutableSequence", (elem_type,), is_protocol=True)
                if not self.protocols.type_conforms_to_protocol(obj_type, mutable_seq):
                    raise self.ctx.error(f"Cannot assign to elements of {obj_type} (read-only)", stmt)

        # Prevent assignment through ConstPtr (read-only pointer)
        if isinstance(stmt.target, TpyName):
            pass  # TpyName targets are fine
        else:
            if isinstance(stmt.target, TpyFieldAccess):
                obj_type = self.ctx.get_expr_type(stmt.target.obj)
                if isinstance(obj_type, ConstPtrType):
                    raise self.ctx.error("Cannot assign through ConstPtr (read-only pointer)", stmt)

        # When reassigning a variable with IntLiteralType to a concrete integer type,
        # update the variable's type to the more specific type
        if isinstance(stmt.target, TpyName) and isinstance(target_type, IntLiteralType):
            if isinstance(value_type, (Int32Type, BigIntType)):
                self.ctx.current_scope.define(stmt.target.name, value_type)
                if self.ctx.current_ns:
                    self.ctx.current_ns.update_variable_type(stmt.target.name, value_type)
                self.ctx.set_expr_type(stmt.target, value_type)
                # Update var_types so codegen knows the resolved type
                var_decl = self.ctx.var_decl_by_name.get(stmt.target.name)
                if var_decl:
                    self.ctx.var_types[id(var_decl)] = value_type
                return

        stmt.value = self.compat.coerce_expr(stmt.value, value_type, target_type, "assignment",
                                              coercion_ctx=CoercionContext.ASSIGN)

        if isinstance(stmt.target, TpyFieldAccess):
            if self.compat.needs_copy_warning(stmt.value, target_type):
                if isinstance(target_type, TypeParamRef):
                    msg = f"may copy {target_type} into field if not a value type; use copy() to make this explicit"
                else:
                    msg = f"copies {value_type} into field; use copy() to make this explicit"
                self.ctx.warning(msg, stmt)

        if isinstance(stmt.target, TpySubscript):
            if self.compat.needs_copy_warning(stmt.value, target_type):
                if isinstance(target_type, TypeParamRef):
                    msg = f"may copy {target_type} into container if not a value type; use copy() to make this explicit"
                else:
                    msg = f"copies {value_type} into container; use copy() to make this explicit"
                self.ctx.warning(msg, stmt)

    def _analyze_aug_assign(self, stmt: TpyAugAssign) -> None:
        """Analyze an augmented assignment (+=, -=, etc.)."""
        from .operators import OperatorResolver
        target_type = self.expr.analyze_expr(stmt.target)
        value_type = self.expr.analyze_expr(stmt.value)
        # Both must be numeric types for arithmetic augmented assignment
        if not isinstance(target_type, (Int32Type, BigIntType, IntLiteralType, FloatType)):
            raise SemanticError(f"Augmented assignment target must be a numeric type, got {target_type}")
        if not isinstance(value_type, (Int32Type, BigIntType, IntLiteralType, FloatType)):
            raise SemanticError(f"Augmented assignment value must be a numeric type, got {value_type}")
        # Special case: Int32 += BigInt should use Int32 ops (value gets converted to Int32)
        # This preserves checked arithmetic and avoids unnecessary promotion to BigInt
        resolve_value_type = value_type
        if isinstance(target_type, Int32Type) and isinstance(value_type, BigIntType):
            resolve_value_type = INT32
        # Resolve the binary operation for codegen
        operators = OperatorResolver(self.ctx)
        if result := operators.resolve_binop(target_type, stmt.op, resolve_value_type):
            stmt.resolved_binop = result
