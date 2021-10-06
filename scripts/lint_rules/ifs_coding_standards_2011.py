# pylint: disable=invalid-all-format
"""
Implementation of rules in the IFS coding standards document (2011) for loki-lint.
"""

from pathlib import Path
from collections import defaultdict
import re

from loki import (
    Visitor, FindNodes, ExpressionFinder, FindExpressionRoot, ExpressionRetriever,
    flatten, as_tuple, strip_inline_comments, Module, Subroutine, BasicType
)
from loki.lint import GenericRule, RuleType
import loki.ir as ir
from loki.expression import symbols as sym


class CodeBodyRule(GenericRule):  # Coding standards 1.3

    type = RuleType.WARN

    docs = {
        'id': '1.3',
        'title': ('Rules for Code Body: '
                  'Nesting of conditional blocks should not be more than {max_nesting_depth} '
                  'levels deep;'),
    }

    config = {
        'max_nesting_depth': 3,
    }

    class NestingDepthVisitor(Visitor):

        @classmethod
        def default_retval(cls):
            return []

        def __init__(self, max_nesting_depth):
            super().__init__()
            self.max_nesting_depth = max_nesting_depth

        def visit(self, o, *args, **kwargs):
            return flatten(super().visit(o, *args, **kwargs))

        def visit_Conditional(self, o, **kwargs):
            level = kwargs.pop('level', 0)
            too_deep = []
            if level >= self.max_nesting_depth and not getattr(o, 'inline', False):
                too_deep = [o]
            too_deep += self.visit(o.body, level=level + 1, **kwargs)
            if o.has_elseif:
                too_deep += self.visit(o.else_body, level=level, **kwargs)
            else:
                too_deep += self.visit(o.else_body, level=level + 1, **kwargs)
            return too_deep

        def visit_MultiConditional(self, o, **kwargs):
            level = kwargs.pop('level', 0)
            too_deep = []
            if level >= self.max_nesting_depth and not getattr(o, 'inline', False):
                too_deep = [o]
            too_deep += self.visit(o.bodies, level=level + 1, **kwargs)
            too_deep += self.visit(o.else_body, level=level + 1, **kwargs)
            return too_deep

    @classmethod
    def check_subroutine(cls, subroutine, rule_report, config):
        '''Check the code body: Nesting of conditional blocks.'''
        too_deep = cls.NestingDepthVisitor(config['max_nesting_depth']).visit(subroutine.body)
        fmt_string = 'Nesting of conditionals exceeds limit of {}.'
        msg = fmt_string.format(config['max_nesting_depth'])
        for node in too_deep:
            rule_report.add(msg, node)


class ModuleNamingRule(GenericRule):  # Coding standards 1.5

    type = RuleType.WARN

    docs = {
        'id': '1.5',
        'title': ('Naming Schemes for Modules: All modules should end with "_mod". '
                  'Module filename should match the name of the module it contains.'),
    }

    @classmethod
    def check_module(cls, module, rule_report, config):
        '''Check the module name and the name of the source file.'''
        if not module.name.lower().endswith('_mod'):
            fmt_string = 'Name of module "{}" should end with "_mod".'
            msg = fmt_string.format(module.name)
            rule_report.add(msg, module)

        if module.source.file:
            path = Path(module.source.file)
            if module.name.lower() != path.stem.lower():
                fmt_string = 'Module filename "{}" does not match module name "{}".'
                msg = fmt_string.format(path.name, module.name)
                rule_report.add(msg, module)


class DrHookRule(GenericRule):  # Coding standards 1.9

    type = RuleType.SERIOUS

    docs = {
        'id': '1.9',
        'title': 'Rules for DR_HOOK',
    }

    non_exec_nodes = (ir.Comment, ir.CommentBlock, ir.Pragma, ir.PreprocessorDirective)

    @classmethod
    def _find_lhook_conditional(cls, ast, is_reversed=False):
        cond = None
        for node in reversed(ast) if is_reversed else ast:
            if isinstance(node, ir.Conditional):
                if node.condition == 'LHOOK':
                    cond = node
                    break
            elif not isinstance(node, cls.non_exec_nodes):
                # Break if executable statement encountered
                break
        return cond

    @classmethod
    def _find_lhook_call(cls, cond, is_reversed=False):
        call = None
        if cond:
            # We use as_tuple here because the conditional can be inline and then its body is not
            # iterable but a single node (e.g., CallStatement)
            body = reversed(as_tuple(cond.body)) if is_reversed else as_tuple(cond.body)
            for node in body:
                if isinstance(node, ir.CallStatement) and node.name == 'DR_HOOK':
                    call = node
                elif not isinstance(node, cls.non_exec_nodes):
                    # Break if executable statement encountered
                    break
        return call

    @staticmethod
    def _get_string_argument(scope):
        string_arg = scope.name.upper()
        while hasattr(scope, 'parent') and scope.parent:
            scope = scope.parent
            if isinstance(scope, Subroutine):
                string_arg = scope.name.upper() + '%' + string_arg
            elif isinstance(scope, Module):
                string_arg = scope.name.upper() + ':' + string_arg
        return string_arg

    @classmethod
    def _check_lhook_call(cls, call, subroutine, rule_report, pos='First'):
        if call is None:
            fmt_string = '{} executable statement must be call to DR_HOOK.'
            msg = fmt_string.format(pos)
            rule_report.add(msg, subroutine)
        elif call.arguments:
            string_arg = cls._get_string_argument(subroutine)
            if not isinstance(call.arguments[0], sym.StringLiteral) or \
                    call.arguments[0].value.upper() != string_arg:
                fmt_string = 'String argument to DR_HOOK call should be "{}".'
                msg = fmt_string.format(string_arg)
                rule_report.add(msg, call)
            second_arg = {'First': '0', 'Last': '1'}
            if not (len(call.arguments) > 1 and isinstance(call.arguments[1], sym.IntLiteral) and
                    str(call.arguments[1].value) == second_arg[pos]):
                fmt_string = 'Second argument to DR_HOOK call should be "{}".'
                msg = fmt_string.format(second_arg[pos])
                rule_report.add(msg, call)
            if not (len(call.arguments) > 2 and call.arguments[2] == 'ZHOOK_HANDLE'):
                msg = 'Third argument to DR_HOOK call should be "ZHOOK_HANDLE".'
                rule_report.add(msg, call)

    @classmethod
    def check_subroutine(cls, subroutine, rule_report, config):
        '''Check that first and last executable statements in the subroutine
        are conditionals with calls to DR_HOOK in their body and that the
        correct arguments are given to the call.'''
        # Extract the AST for the subroutine body
        ast = subroutine.body
        if isinstance(ast, ir.Section):
            ast = ast.body
        ast = flatten(ast)

        # Look for conditionals in subroutine body
        first_cond = cls._find_lhook_conditional(ast)
        last_cond = cls._find_lhook_conditional(ast, is_reversed=True)

        # Find calls to DR_HOOK
        first_call = cls._find_lhook_call(first_cond)
        last_call = cls._find_lhook_call(last_cond, is_reversed=True)

        cls._check_lhook_call(first_call, subroutine, rule_report)
        cls._check_lhook_call(last_call, subroutine, rule_report, pos='Last')


class LimitSubroutineStatementsRule(GenericRule):  # Coding standards 2.2

    type = RuleType.WARN

    docs = {
        'id': '2.2',
        'title': 'Subroutines should have no more than {max_num_statements} executable statements.',
    }

    config = {
        'max_num_statements': 300
    }

    # List of nodes that are considered executable statements
    exec_nodes = (
        ir.Assignment, ir.MaskedStatement, ir.Intrinsic, ir.Allocation,
        ir.Deallocation, ir.Nullify, ir.CallStatement
    )

    # Pattern for intrinsic nodes that are allowed as non-executable statements
    match_non_exec_intrinsic_node = re.compile(r'\s*(?:PRINT|FORMAT)', re.I)

    @classmethod
    def check_subroutine(cls, subroutine, rule_report, config):
        '''Count the number of nodes in the subroutine and check if they exceed
        a given maximum number.
        '''
        # Count total number of executable nodes
        nodes = FindNodes(cls.exec_nodes).visit(subroutine.ir)
        num_nodes = len(nodes)
        # Subtract number of non-exec intrinsic nodes
        intrinsic_nodes = filter(lambda node: isinstance(node, ir.Intrinsic), nodes)
        num_nodes -= sum(1 for _ in filter(
            lambda node: cls.match_non_exec_intrinsic_node.match(node.text), intrinsic_nodes))

        if num_nodes > config['max_num_statements']:
            fmt_string = 'Subroutine has {} executable statements (should not have more than {})'
            msg = fmt_string.format(num_nodes, config['max_num_statements'])
            rule_report.add(msg, subroutine)


class MaxDummyArgsRule(GenericRule):  # Coding standards 3.6

    type = RuleType.INFO

    docs = {
        'id': '3.6',
        'title': 'Routines should have no more than {max_num_arguments} dummy arguments.',
    }

    config = {
        'max_num_arguments': 50
    }

    @classmethod
    def check_subroutine(cls, subroutine, rule_report, config):
        """
        Count the number of dummy arguments and report if given
        maximum number exceeded.
        """
        num_arguments = len(subroutine.arguments)
        if num_arguments > config['max_num_arguments']:
            fmt_string = 'Subroutine has {} dummy arguments (should not have more than {})'
            msg = fmt_string.format(num_arguments, config['max_num_arguments'])
            rule_report.add(msg, subroutine)


class MplCdstringRule(GenericRule):  # Coding standards 3.12

    type = RuleType.SERIOUS

    docs = {
        'id': '3.12',
        'title': 'Calls to MPL subroutines should provide a "CDSTRING" identifying the caller.',
    }

    @classmethod
    def check_subroutine(cls, subroutine, rule_report, config):
        '''Check all calls to MPL subroutines for a CDSTRING.'''
        for call in FindNodes(ir.CallStatement).visit(subroutine.ir):
            if str(call.name).upper().startswith('MPL_'):
                for kw, _ in call.kwarguments:
                    if kw.upper() == 'CDSTRING':
                        break
                else:
                    fmt_string = 'No "CDSTRING" provided in call to {}'
                    msg = fmt_string.format(call.name)
                    rule_report.add(msg, call)


class ImplicitNoneRule(GenericRule):  # Coding standards 4.4

    type = RuleType.SERIOUS

    docs = {
        'id': '4.4',
        'title': '"IMPLICIT NONE" is mandatory in all routines.',
    }

    _regex = re.compile(r'implicit\s+none\b', re.I)

    @staticmethod
    def check_for_implicit_none(ast):
        """
        Check for intrinsic nodes that match the regex.
        """
        for intr in FindNodes(ir.Intrinsic).visit(ast):
            if ImplicitNoneRule._regex.match(intr.text):
                break
        else:
            return False
        return True

    @classmethod
    def check_subroutine(cls, subroutine, rule_report, config):
        """
        Check for IMPLICIT NONE in the subroutine's spec or any enclosing
        scope.
        """
        found_implicit_none = cls.check_for_implicit_none(subroutine.ir)

        # Check if enclosing scopes contain implicit none
        scope = subroutine.parent
        while scope and not found_implicit_none:
            if hasattr(scope, 'spec') and scope.spec:
                found_implicit_none = cls.check_for_implicit_none(scope.spec)
            scope = scope.parent if hasattr(scope, 'parent') else None

        if not found_implicit_none:
            # No 'IMPLICIT NONE' intrinsic node was found
            rule_report.add('No "IMPLICIT NONE" found', subroutine)


class ExplicitKindRule(GenericRule):  # Coding standards 4.7

    type = RuleType.SERIOUS

    docs = {
        'id': '4.7',
        'title': ('Variables and constants must be declared with explicit kind, using the kinds '
                  'defined in "PARKIND1" and "PARKIND2".'),
    }

    config = {
        'declaration_types': ['INTEGER', 'REAL'],
        'constant_types': ['REAL'],  # Coding standards document includes INTEGERS here
        'allowed_type_kinds': {
            'INTEGER': ['JPIM', 'JPIT', 'JPIB', 'JPIA', 'JPIS', 'JPIH'],
            'REAL': ['JPRB', 'JPRM', 'JPRS', 'JPRT', 'JPRH', 'JPRD', 'JPHOOK']
        }
    }

    @staticmethod
    def check_kind_declarations(subroutine, types, allowed_type_kinds, rule_report):
        '''Helper function that carries out the check for explicit kind specification
        on all declarations.
        '''
        for var in subroutine.variables:
            if var.type.dtype in types:
                if not var.type.kind:
                    rule_report.add('"{}" without explicit KIND declared.'.format(var), var)
                elif allowed_type_kinds.get(var.type.dtype) and \
                        var.type.kind not in allowed_type_kinds[var.type.dtype]:
                    rule_report.add(
                        '"{}" is not an allowed KIND value for "{}".'.format(var.type.kind, var),
                        var)

    @staticmethod
    def check_kind_literals(subroutine, types, allowed_type_kinds, rule_report):
        '''Helper function that carries out the check for explicit kind specification
        on all literals.
        '''
        # Custom retriever that yields the literal types specified in config and stops
        # recursion on loop ranges and array subscripts
        # (to avoid warnings about integer constants in these cases)
        excl_types = (sym.Array, sym.Range)
        retriever = ExpressionRetriever(query=lambda e: isinstance(e, types),
                                        recurse_query=lambda e: not isinstance(e, excl_types))
        finder = ExpressionFinder(unique=False, retrieve=retriever.retrieve, with_ir_node=True)

        for _, exprs in finder.visit(subroutine.ir):
            for literal in exprs:
                if not literal.kind:
                    rule_report.add('"{}" without explicit KIND declared.'.format(literal), literal)
                elif allowed_type_kinds.get(literal.__class__) and \
                        str(literal.kind).upper() not in allowed_type_kinds[literal.__class__]:
                    rule_report.add(
                        '"{}" is not an allowed KIND value for "{}".'.format(literal.kind, literal),
                        literal)

    @classmethod
    def check_subroutine(cls, subroutine, rule_report, config):
        '''Check for explicit kind information in constants and
        variable declarations.
        '''
        # 1. Check variable declarations for explicit KIND
        #
        # When we check variable type information, we have BasicType values to identify
        # whether a variable is REAL, INTEGER, ... Therefore, we create a map that uses
        # the corresponding BasicType values as keys to look up allowed kinds for each type.
        # Since the case does not matter, we convert all allowed type kinds to upper case.
        types = tuple(BasicType.from_str(name) for name in config['declaration_types'])
        allowed_type_kinds = {}
        if config.get('allowed_type_kinds'):
            allowed_type_kinds = {BasicType.from_str(name): [kind.upper() for kind in kinds]
                                  for name, kinds in config['allowed_type_kinds'].items()}

        cls.check_kind_declarations(subroutine, types, allowed_type_kinds, rule_report)

        # 2. Check constants for explicit KIND
        #
        # Constants are represented by an instance of some Literal class, which directly
        # gives us their type. Therefore, we create a map that uses the corresponding
        # Literal types as keys to look up allowed kinds for each type. Again, we
        # convert all allowed type kinds to upper case.
        type_map = {'INTEGER': sym.IntLiteral, 'REAL': sym.FloatLiteral,
                    'LOGICAL': sym.LogicLiteral, 'CHARACTER': sym.StringLiteral}
        types = tuple(type_map[name] for name in config['constant_types'])
        if config.get('allowed_type_kinds'):
            allowed_type_kinds = {type_map[name]: [kind.upper() for kind in kinds]
                                  for name, kinds in config['allowed_type_kinds'].items()}

        cls.check_kind_literals(subroutine, types, allowed_type_kinds, rule_report)


class BannedStatementsRule(GenericRule):  # Coding standards 4.11

    type = RuleType.WARN

    docs = {
        'id': '4.11',
        'title': 'Banned statements.',
    }

    config = {
        'banned': ['STOP', 'PRINT', 'RETURN', 'ENTRY', 'DIMENSION',
                   'DOUBLE PRECISION', 'COMPLEX', 'GO TO', 'CONTINUE',
                   'FORMAT', 'COMMON', 'EQUIVALENCE'],
    }

    @classmethod
    def check_subroutine(cls, subroutine, rule_report, config):
        '''Check for banned statements in intrinsic nodes.'''
        for intr in FindNodes(ir.Intrinsic).visit(subroutine.ir):
            for keyword in config['banned']:
                if keyword.lower() in intr.text.lower():
                    msg = 'Banned keyword "{}"'.format(keyword)
                    rule_report.add(msg, intr)


class Fortran90OperatorsRule(GenericRule):  # Coding standards 4.15

    type = RuleType.WARN

    docs = {
        'id': '4.15',
        'title': 'Use Fortran 90 comparison operators.'
    }

    fixable = True

    '''
    Regex patterns for each operator that match F77 and F90 operators as
    named groups, thus allowing to easily find out which operator was used.
    '''
    _op_patterns = {
        '==': re.compile(r'(?P<f77>\.eq\.)|(?P<f90>==)', re.I),
        '!=': re.compile(r'(?P<f77>\.ne\.)|(?P<f90>/=)', re.I),
        '>=': re.compile(r'(?P<f77>\.ge\.)|(?P<f90>>=)', re.I),
        '<=': re.compile(r'(?P<f77>\.le\.)|(?P<f90><=)', re.I),
        '>': re.compile(r'(?P<f77>\.gt\.)|(?P<f90>>(?!=))', re.I),
        '<': re.compile(r'(?P<f77>\.lt\.)|(?P<f90><(?!=))', re.I),
    }

    @classmethod
    def check_subroutine(cls, subroutine, rule_report, config):
        '''Check for the use of Fortran 90 comparison operators.'''
        # We extract all `Comparison` expression nodes, grouped by the IR node they are in.
        # Then we run through all such pairs and check the symbol used in the source string.
        retriever = ExpressionRetriever(lambda e: isinstance(e, sym.Comparison))
        finder = ExpressionFinder(unique=False, retrieve=retriever.retrieve, with_ir_node=True)
        for node, expr_list in finder.visit(subroutine.ir):
            # First, we group all the expressions found in this node by their expression root
            # (This is mostly required for Conditionals/MultiConditionals, where the different
            #  if-elseif-cases or select values are on different source lines)
            root_expr_map = defaultdict(list)
            for expr in expr_list:
                expr_root = FindExpressionRoot(expr).visit(node)[0]
                if expr_root.source and expr_root.source.string:
                    # Include only if we have source string information for this node
                    root_expr_map[expr_root] += [expr]

            # Then we look at the comparison operators for each expression root and match
            # them directly in the source string
            for expr_root, exprs in root_expr_map.items():
                # For each comparison operator, check if F90 or F77 operators are matched
                for op in sorted({op.operator for op in exprs}):
                    source_string = strip_inline_comments(expr_root.source.string)
                    matches = cls._op_patterns[op].findall(source_string)
                    for f77, _ in matches:
                        if f77:
                            fmt_string = 'Use Fortran 90 comparison operator "{}" instead of "{}".'
                            msg = fmt_string.format(op if op != '!=' else '/=', f77)
                            rule_report.add(msg, expr_root)

    @classmethod
    def fix_subroutine(cls, subroutine, rule_report, config):
        '''Replace by Fortran 90 comparison operators.'''
        # We only have to invalidate the source string for the expression. This will cause the
        # backend to regenerate the source string for that node and use Fortran 90 operators
        # automatically
        mapper = {}
        for report in rule_report.problem_reports:
            new_expr = report.location
            new_expr.update_metadata({'source': None})
            mapper[report.location] = new_expr
        return mapper


# Create the __all__ property of the module to contain only the rule names
__all__ = tuple(name for name in dir() if name.endswith('Rule') and name != 'GenericRule')
