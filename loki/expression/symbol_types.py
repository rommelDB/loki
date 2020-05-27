import weakref
from collections import OrderedDict
import pymbolic.primitives as pmbl
from six.moves import intern

from loki.tools import as_tuple
from loki.types import DataType, SymbolType
from loki.expression.mappers import LokiStringifyMapper


__all__ = ['ExprMetadataMixin', 'Scalar', 'Array', 'Variable',
           'FloatLiteral', 'IntLiteral', 'LogicLiteral', 'StringLiteral', 'Literal', 'LiteralList',
           'Sum', 'Product', 'Quotient', 'Power', 'Comparison', 'LogicalAnd', 'LogicalOr',
           'LogicalNot', 'InlineCall', 'Cast', 'Range', 'LoopRange', 'RangeIndex', 'ArraySubscript']


# pylint: disable=abstract-method


class ExprMetadataMixin:
    """
    Meta-data annotations for expression tree nodes.
    """

    def __init__(self, *args, **kwargs):
        self._metadata = {
            'source': kwargs.pop('source', None)
        }
        super().__init__(*args, **kwargs)

    def get_metadata(self):
        return self._metadata.copy()

    def update_metadata(self, data):
        self._metadata.update(data)

    @property
    def source(self):
        return self._metadata['source']


class Scalar(ExprMetadataMixin, pmbl.Variable):
    """
    Expression node for scalar variables (and other algebraic leaves).

    It is always associated with a given scope (typically a class:``Subroutine``)
    where the corresponding `symbol_table` is found with its type.

    Warning: Providing a type overwrites the corresponding entry in the symbol table.
    This is due to the assumption that we might have encountered a variable name before
    knowing about its declaration and thus treat the latest given type information as
    the one that is most up-to-date.
    """

    def __init__(self, name, scope, type=None, parent=None, initial=None, **kwargs):
        # Stop complaints about `type` in this function
        # pylint: disable=redefined-builtin
        super(Scalar, self).__init__(name, **kwargs)

        self._scope = weakref.ref(scope)
        if type is None:
            # Insert the deferred type in the type table only if it does not exist
            # yet (necessary for deferred type definitions, e.g., derived types in header or
            # parameters from other modules)
            self.scope.setdefault(self.name, SymbolType(DataType.DEFERRED))
        elif type is not self.scope.lookup(self.name):
            # If the type information does already exist and is identical (not just
            # equal) we don't update it. This makes sure that we don't create double
            # entries for variables inherited from a parent scope
            self.type = type.clone()
        self.parent = parent
        self.initial = initial

    @property
    def scope(self):
        """
        The object corresponding to the symbols scope.
        """
        return self._scope()

    @property
    def basename(self):
        """
        The symbol name without the qualifier from the parent.
        """
        idx = self.name.rfind('%')
        return self.name[idx+1:]

    @property
    def type(self):
        """
        Internal representation of the declared data type.
        """
        return self.scope.lookup(self.name)

    @type.setter
    def type(self, value):
        self.scope[self.name] = value

    def __getinitargs__(self):
        args = [('scope', self.scope)]
        if self.parent:
            args += [('parent', self.parent)]
        return super().__getinitargs__() + tuple(args)

    mapper_method = intern('map_scalar')

    def make_stringifier(self, originating_stringifier=None):
        return LokiStringifyMapper()

    def clone(self, **kwargs):
        """
        Replicate the :class:`Scalar` variable with the provided overrides.
        """
        # Add existing meta-info to the clone arguments, only if we have them.
        if self.name and 'name' not in kwargs:
            kwargs['name'] = self.name
        if self.scope and 'scope' not in kwargs:
            kwargs['scope'] = self.scope
        if self.type and 'type' not in kwargs:
            kwargs['type'] = self.type
        if self.parent and 'parent' not in kwargs:
            kwargs['parent'] = self.parent
        if self.initial and 'initial' not in kwargs:
            kwargs['initial'] = self.initial

        return Variable(**kwargs)


class Array(ExprMetadataMixin, pmbl.Variable):
    """
    Expression node for array variables.

    It can have associated dimensions (i.e., the indexing/slicing when accessing entries),
    which can be a :class:`RangeIndex` or an expression or a :class:`Literal` or
    a :class:`Scalar`

    Shape, data type and parent information are part of the type.

    Warning: Providing a type overwrites the corresponding entry in the symbol table.
    This is due to the assumption that we might have encountered a variable name before
    knowing about its declaration and thus treat the latest given type information as
    the one that is most up-to-date.
    """

    def __init__(self, name, scope, type=None, parent=None, dimensions=None,
                 initial=None, **kwargs):
        # Stop complaints about `type` in this function
        # pylint: disable=redefined-builtin
        super(Array, self).__init__(name, **kwargs)

        self._scope = weakref.ref(scope)
        if type is None:
            # Insert the defered type in the type table only if it does not exist
            # yet (necessary for deferred type definitions)
            self.scope.setdefault(self.name, SymbolType(DataType.DEFERRED))
        elif type is not self.scope.lookup(self.name):
            # If the type information does already exist and is identical (not just
            # equal) we don't update it. This makes sure that we don't create double
            # entries for variables inherited from a parent scope
            self.type = type.clone()
        self.parent = parent
        # Ensure dimensions are treated via ArraySubscript objects
        if dimensions is not None and not isinstance(dimensions, ArraySubscript):
            dimensions = ArraySubscript(dimensions)
        self.dimensions = dimensions
        self.initial = initial

    @property
    def scope(self):
        """
        The object corresponding to the symbols scope.
        """
        return self._scope()

    @property
    def basename(self):
        """
        The symbol name without the qualifier from the parent.
        """
        idx = self.name.rfind('%')
        return self.name[idx+1:]

    @property
    def type(self):
        """
        Internal representation of the declared data type.
        """
        return self.scope.lookup(self.name)

    @type.setter
    def type(self, value):
        self.scope[self.name] = value

    @property
    def dimensions(self):
        """
        Symbolic representation of the dimensions or indices.
        """
        return self._dimensions

    @dimensions.setter
    def dimensions(self, value):
        self._dimensions = value

    @property
    def shape(self):
        """
        Original allocated shape of the variable as a tuple of dimensions.
        """
        return self.type.shape

    @shape.setter
    def shape(self, value):
        self.type.shape = value

    def __getinitargs__(self):
        args = [('scope', self.scope)]
        if self.dimensions:
            args += [('dimensions', self.dimensions)]
        if self.parent:
            args += [('parent', self.parent)]
        return super().__getinitargs__() + tuple(args)

    mapper_method = intern('map_array')

    def make_stringifier(self, originating_stringifier=None):
        return LokiStringifyMapper()

    def clone(self, **kwargs):
        """
        Replicate the :class:`Array` variable with the provided overrides.

        Note, if :param dimensions: is provided as ``None``, a
        :class:`Scalar` variable will be created.
        """
        # Add existing meta-info to the clone arguments, only if we have them.
        if self.name and 'name' not in kwargs:
            kwargs['name'] = self.name
        if self.scope and 'scope' not in kwargs:
            kwargs['scope'] = self.scope
        if self.dimensions and 'dimensions' not in kwargs:
            kwargs['dimensions'] = self.dimensions
        if self.type and 'type' not in kwargs:
            kwargs['type'] = self.type
        if self.parent and 'parent' not in kwargs:
            kwargs['parent'] = self.parent
        if self.initial and 'initial' not in kwargs:
            kwargs['initial'] = self.initial

        return Variable(**kwargs)


class Variable:
    """
    A symbolic object representing either a :class:`Scalar` or a :class:`Array`
    variable in arithmetic expressions.

    Note, that this is only a convenience constructor that always returns either
    a :class:`Scalar` or :class:`Array` variable object.

    Warning: Providing a type overwrites the corresponding entry in the symbol table.
    This is due to the assumption that we might have encountered a variable name before
    knowing about its declaration and thus treat the latest given type information as
    the one that is most up-to-date.
    """

    def __new__(cls, **kwargs):
        """
        1st-level variables creation with name injection via the object class
        """
        name = kwargs['name']
        scope = kwargs['scope']
        _type = kwargs.setdefault('type', scope.lookup(name))

        dimensions = kwargs.pop('dimensions', None)
        shape = _type.shape if _type is not None else None

        if dimensions is None and not shape:
            obj = Scalar(**kwargs)
        else:
            obj = Array(dimensions=dimensions, **kwargs)

        obj = cls.instantiate_derived_type_variables(obj)
        return obj

    @classmethod
    def instantiate_derived_type_variables(cls, obj):
        """
        If the type of obj is a derived type then its list of variables is possibly from
        the declarations inside a TypeDef and as such, the variables are referring to a
        different scope. Thus, we must re-create these variables in the correct scope.
        For the actual instantiation of a variable with that type, we need to create a dedicated
        copy of that type and replace its parent by this object and its list of variables (which
        is an OrderedDict of SymbolTypes) by a list of Variable instances.
        """
        if obj.type is not None and obj.type.dtype == DataType.DERIVED_TYPE:
            if obj.type.variables and next(iter(obj.type.variables.values())).scope != obj.scope:
                variables = obj.type.variables
                obj.type = obj.type.clone(variables=OrderedDict())
                for k, v in variables.items():
                    vtype = v.type.clone(parent=obj)
                    vname = '%s%%%s' % (obj.name, v.basename)
                    obj.type.variables[k] = Variable(name=vname, scope=obj.scope, type=vtype)
        return obj


class _Literal(pmbl.Leaf):
    """
    Helper base class for literals to overcome the problem of a disfunctional
    __getinitargs__ in py:class:`pymbolic.primitives.Leaf`.
    """

    def __getinitargs__(self):
        return ()


class FloatLiteral(ExprMetadataMixin, _Literal):
    """
    A floating point constant in an expression.

    It can have a specific type associated, which can be used to cast the constant to that
    type in the output of the backend.
    """

    def __init__(self, value, **kwargs):
        # We store float literals as strings to make sure no information gets
        # lost in the conversion
        self.value = str(value)
        self.kind = kwargs.pop('kind', None)
        super(FloatLiteral, self).__init__(**kwargs)

    def __getinitargs__(self):
        args = [self.value]
        if self.kind:
            args += [('kind', self.kind)]
        return tuple(args) + super().__getinitargs__()

    mapper_method = intern('map_float_literal')

    def make_stringifier(self, originating_stringifier=None):
        return LokiStringifyMapper()


class IntLiteral(ExprMetadataMixin, _Literal):
    """
    An integer constant in an expression.

    It can have a specific type associated, which can be used to cast the constant to that
    type in the output of the backend.
    """

    def __init__(self, value, **kwargs):
        self.value = int(value)
        self.kind = kwargs.pop('kind', None)
        super(IntLiteral, self).__init__(**kwargs)

    def __getinitargs__(self):
        args = [self.value]
        if self.kind:
            args += [('kind', self.kind)]
        return tuple(args) + super().__getinitargs__()

    mapper_method = intern('map_int_literal')

    def make_stringifier(self, originating_stringifier=None):
        return LokiStringifyMapper()


class LogicLiteral(ExprMetadataMixin, _Literal):
    """
    A boolean constant in an expression.
    """

    def __init__(self, value, **kwargs):
        self.value = value.lower() in ('true', '.true.')
        super(LogicLiteral, self).__init__(**kwargs)

    def __getinitargs__(self):
        return (self.value,) + super().__getinitargs__()

    mapper_method = intern('map_logic_literal')

    def make_stringifier(self, originating_stringifier=None):
        return LokiStringifyMapper()


class StringLiteral(ExprMetadataMixin, _Literal):
    """
    A string.
    """

    def __init__(self, value, **kwargs):
        # Remove quotation marks
        if value[0] == value[-1] and value[0] in '"\'':
            value = value[1:-1]

        self.value = value

        super(StringLiteral, self).__init__(**kwargs)

    def __getinitargs__(self):
        return (self.value,) + super().__getinitargs__()

    mapper_method = intern('map_string_literal')

    def make_stringifier(self, originating_stringifier=None):
        return LokiStringifyMapper()


class Literal:
    """
    A factory class that instantiates the appropriate :class:`*Literal` type for
    a given value.

    This always returns a :class:`IntLiteral`, :class:`FloatLiteral`, :class:`StringLiteral`,
    or :class:`LogicLiteral`.
    """

    @staticmethod
    def _from_literal(value, **kwargs):

        cls_map = {DataType.INTEGER: IntLiteral, DataType.REAL: FloatLiteral,
                   DataType.LOGICAL: LogicLiteral, DataType.CHARACTER: StringLiteral}

        _type = kwargs.pop('type', None)
        if _type is None:
            if isinstance(value, int):
                _type = DataType.INTEGER
            elif isinstance(value, float):
                _type = DataType.REAL
            elif isinstance(value, str):
                if str(value).lower() in ('.true.', 'true', '.false.', 'false'):
                    _type = DataType.LOGICAL
                else:
                    _type = DataType.CHARACTER

        return cls_map[_type](value, **kwargs)

    def __new__(cls, value, **kwargs):
        try:
            obj = cls._from_literal(value, **kwargs)
        except KeyError:
            # Let Pymbolic figure our what we're dealing with
            # pylint: disable=import-outside-toplevel
            from pymbolic import parse
            obj = parse(value)

            # Make sure we catch elementary literals
            if not isinstance(obj, pmbl.Expression):
                obj = cls._from_literal(obj, **kwargs)

        # And attach our own meta-data
        if hasattr(obj, 'kind'):
            obj.kind = kwargs.get('kind', None)
        return obj


class LiteralList(ExprMetadataMixin, pmbl.AlgebraicLeaf):
    """
    A list of constant literals, e.g., as used in Array Initialization Lists.
    """

    def __init__(self, values, **kwargs):
        self.elements = values
        super(LiteralList, self).__init__(**kwargs)

    mapper_method = intern('map_literal_list')

    def make_stringifier(self, originating_stringifier=None):
        return LokiStringifyMapper()

    def __getinitargs__(self):
        return ('[%s]' % (','.join(repr(c) for c in self.elements)),) + super().__getinitargs__()


class Sum(ExprMetadataMixin, pmbl.Sum):
    """Representation of a sum."""


class Product(ExprMetadataMixin, pmbl.Product):
    """Representation of a product."""


class Quotient(ExprMetadataMixin, pmbl.Quotient):
    """Representation of a quotient."""


class Power(ExprMetadataMixin, pmbl.Power):
    """Representation of a power."""


class Comparison(ExprMetadataMixin, pmbl.Comparison):
    """Representation of a comparison operation."""


class LogicalAnd(ExprMetadataMixin, pmbl.LogicalAnd):
    """Representation of an 'and' in a logical expression."""


class LogicalOr(ExprMetadataMixin, pmbl.LogicalOr):
    """Representation of an 'or' in a logical expression."""


class LogicalNot(ExprMetadataMixin, pmbl.LogicalNot):
    """Representation of a negation in a logical expression."""


class InlineCall(ExprMetadataMixin, pmbl.CallWithKwargs):
    """
    Internal representation of an in-line function call.
    """

    def __init__(self, function, parameters=None, kw_parameters=None, **kwargs):
        function = pmbl.make_variable(function)
        parameters = parameters or ()
        kw_parameters = kw_parameters or {}

        super().__init__(function=function, parameters=parameters,
                         kw_parameters=kw_parameters, **kwargs)

    mapper_method = intern('map_inline_call')

    def make_stringifier(self, originating_stringifier=None):
        return LokiStringifyMapper()

    @property
    def name(self):
        return self.function.name


class Cast(ExprMetadataMixin, pmbl.Call):
    """
    Internal representation of a data type cast.
    """

    def __init__(self, name, expression, kind=None, **kwargs):
        self.kind = kind
        super().__init__(pmbl.make_variable(name), as_tuple(expression), **kwargs)

    mapper_method = intern('map_cast')

    def make_stringifier(self, originating_stringifier=None):
        return LokiStringifyMapper()

    @property
    def name(self):
        return self.function.name


class Range(ExprMetadataMixin, pmbl.Slice):
    """
    Internal representation of a loop or index range.
    """

    def __init__(self, children, **kwargs):
        assert len(children) in (2, 3)
        if len(children) == 2:
            children += (None,)
        super().__init__(children, **kwargs)

    mapper_method = intern('map_range')

    def make_stringifier(self, originating_stringifier=None):
        return LokiStringifyMapper()

    @property
    def lower(self):
        return self.start

    @property
    def upper(self):
        return self.stop


class RangeIndex(Range):
    """
    Internal representation of a subscript range.
    """

    mapper_method = intern('map_range_index')


class LoopRange(Range):
    """
    Internal representation of a loop range.
    """

    mapper_method = intern('map_loop_range')


class ArraySubscript(ExprMetadataMixin, pmbl.Subscript):
    """
    Internal representation of an array subscript.
    """

    def __init__(self, index, **kwargs):
        # TODO: have aggregate here?
        super().__init__(None, index, **kwargs)

    def make_stringifier(self, originating_stringifier=None):
        return LokiStringifyMapper()

    mapper_method = intern('map_array_subscript')
