# Copyright (c) 2013, Schneider Electric Buildings AB
# All rights reserved.
#
# Redistribution and use in source and binary forms, with or without
# modification, are permitted provided that the following conditions are met:
#     * Redistributions of source code must retain the above copyright
#       notice, this list of conditions and the following disclaimer.
#     * Redistributions in binary form must reproduce the above copyright
#       notice, this list of conditions and the following disclaimer in the
#       documentation and/or other materials provided with the distribution.
#     * Neither the name of Schneider Electric Buildings AB nor the
#       names of contributors may be used to endorse or promote products
#       derived from this software without specific prior written permission.
#
# THIS SOFTWARE IS PROVIDED BY THE COPYRIGHT HOLDERS AND CONTRIBUTORS "AS IS" AND
# ANY EXPRESS OR IMPLIED WARRANTIES, INCLUDING, BUT NOT LIMITED TO, THE IMPLIED
# WARRANTIES OF MERCHANTABILITY AND FITNESS FOR A PARTICULAR PURPOSE ARE
# DISCLAIMED. IN NO EVENT SHALL THE COPYRIGHT HOLDER OR CONTRIBUTORS BE LIABLE FOR
# ANY DIRECT, INDIRECT, INCIDENTAL, SPECIAL, EXEMPLARY, OR CONSEQUENTIAL DAMAGES
# (INCLUDING, BUT NOT LIMITED TO, PROCUREMENT OF SUBSTITUTE GOODS OR SERVICES;
# LOSS OF USE, DATA, OR PROFITS; OR BUSINESS INTERRUPTION) HOWEVER CAUSED AND
# ON ANY THEORY OF LIABILITY, WHETHER IN CONTRACT, STRICT LIABILITY, OR TORT
# (INCLUDING NEGLIGENCE OR OTHERWISE) ARISING IN ANY WAY OUT OF THE USE OF THIS
# SOFTWARE, EVEN IF ADVISED OF THE POSSIBILITY OF SUCH DAMAGE.

from asn1ate import parser


def build_semantic_model(parse_result):
    """ Build a semantic model of the ASN.1 definition
    from a syntax tree generated by asn1ate.parser.
    """
    root = []
    for token in parse_result:
        _assert_annotated_token(token)
        root.append(_create_sema_node(token))

    return root


def topological_sort(decls):
    """ Algorithm adapted from:
    http://en.wikipedia.org/wiki/Topological_sorting.

    Assumes decls is an iterable of items with two methods:
    - reference_name() -- returns the reference name of the decl
    - references() -- returns an iterable of reference names
    upon which the decl depends.
    """
    graph = dict((d.reference_name(), set(d.references())) for d in decls)

    def has_predecessor(node):
        for predecessor in graph.keys():
            if node in graph[predecessor]:
                return True

        return False

    # Build a topological order of type names
    topological_order = []
    roots = [name for name in graph.keys() if not has_predecessor(name)]

    while roots:
        root = roots.pop()

        # Remove the current node from the graph
        # and collect all new roots (the nodes that
        # were previously only referenced from n)
        successors = graph.pop(root, set())
        roots.extend(successor for successor in successors if not has_predecessor(successor))

        topological_order.insert(0, root)

    if graph:
        raise Exception('Can\'t sort cyclic references: %s' % graph)

    # Sort the actual decls based on the topological order
    return sorted(decls, key=lambda d: topological_order.index(d.reference_name()))


"""
Sema nodes

Concepts in the ASN.1 specification are mirrored here as a simple object model.

Class and member names typically follow the ASN.1 terminology, but there are
some concepts captured which are not expressed in the spec.

Most notably, we build a dependency graph of all types and values in a module,
to allow code generators to build code in dependency order.

All nodes that may be referenced (e.g. types and values) must have a
method called ``reference_name``.

All nodes that may reference other types (e.g. assignments, component types)
must have a method called ``references`` returning the names of all referenced
nodes.

Typically, if you have a ``reference_name``, you must also have a ``references``,
but not necessarily the other way around.
"""

class Module(object):
    def __init__(self, elements):
        module_reference, _, _, _, module_body, _ = elements
        _assert_annotated_token(module_reference)
        _assert_annotated_token(module_body)

        self.name = module_reference.elements[0]
        self.declarations = [_create_sema_node(token) for token in module_body.elements]
        self._user_types = {}

    def user_types(self):
        if not self._user_types:
            # Index all type assignments by name
            for user_defined in self.declarations:
                self._user_types[user_defined.type_name] = user_defined.type_decl

        return self._user_types

    def resolve_type_decl(self, type_decl):
        """ Recursively resolve user-defined types to their
        built-in declaration.
        """
        user_types = self.user_types()

        if isinstance(type_decl, UserDefinedType):
            return self.resolve_type_decl(user_types[type_decl.type_name])
        else:
            return type_decl


    def __str__(self):
        return '%s DEFINITIONS ::=\n' % self.name \
            + 'BEGIN\n' \
            + '\n'.join(map(str, self.declarations)) + '\n' \
            + 'END\n'

    __repr__ = __str__


class TypeAssignment(object):
    def __init__(self, elements):
        assert(len(elements) == 3)
        type_name, _, type_decl = elements
        self.type_name = type_name
        self.type_decl = _create_sema_node(type_decl)

    def reference_name(self):
        return self.type_name

    def references(self):
        return self.type_decl.references()

    def __str__(self):
        return '%s ::= %s' % (self.type_name, self.type_decl)

    __repr__ = __str__


class ValueAssignment(object):
    def __init__(self, elements):
        value_name, type_name, _, value = elements
        self.value_name = ValueReference(value_name.elements) # First token is always a valuereference
        self.type_decl = _create_sema_node(type_name)

        if isinstance(value, parser.AnnotatedToken):
            self.value = _create_sema_node(value) 
        else:
            self.value = value

    def reference_name(self):
        return self.value_name.reference_name()

    def references(self):
        refs = [self.type_decl.reference_name()]
        if isinstance(self.value, ValueReference):
            refs.append(self.value.reference_name())
        else:
            # It's a literal, and they don't play into declaration order.
            pass

        return refs

    def __str__(self):
        return '%s %s ::= %s' % (self.value_name, self.type_decl, self.value)

    __repr__ = __str__


class ValueReference(object):
    def __init__(self, elements):
        self.name = elements[0]

    def reference_name(self):
        return self.name

    def references(self):
        return []

    def __str__(self):
        return self.name

    __repr__ = __str__


class ConstructedType(object):
    def __init__(self, elements):
        type_name, component_tokens = elements
        self.type_name = type_name
        self.components = [_create_sema_node(token) for token in component_tokens]

    def references(self):
        references = []
        for component in self.components:
            references.extend(component.references())
        return references

    def __str__(self):
        component_type_list = ', '.join(map(str, self.components))
        return '%s { %s }' % (self.type_name, component_type_list)

    __repr__ = __str__


class ChoiceType(ConstructedType):
    def __init__(self, elements):
        super(ChoiceType, self).__init__(elements)


class SequenceType(ConstructedType):
    def __init__(self, elements):
        super(SequenceType, self).__init__(elements)


class SequenceOfType(object):
    def __init__(self, elements):
        type_name, type_token = elements
        self.type_name = type_name
        self.type_decl = _create_sema_node(type_token)

    def references(self):
        return self.type_decl.references()

    def __str__(self):
        return '%s %s' % (self.type_name, self.type_decl)

    __repr__ = __str__


class SetOfType(object):
    def __init__(self, elements):
        type_name, type_token = elements
        self.type_name = type_name
        self.type_decl = _create_sema_node(type_token)

    def references(self):
        return self.type_decl.references()

    def __str__(self):
        return '%s %s' % (self.type_name, self.type_decl)

    __repr__ = __str__


class TaggedType(object):
    def __init__(self, elements):
        self.class_name = None
        self.class_number = None
        self.implicit = False

        tag_token = elements[0]
        if type(elements[1]) is parser.AnnotatedToken:
            type_token = elements[1]
        else:
            self.implicit = elements[1] == 'IMPLICIT'
            type_token = elements[2]

        for tag_element in tag_token.elements:
            if tag_element.ty == 'TagClassNumber':
                self.class_number = tag_element.elements[0]
            elif tag_element.ty == 'TagClass':
                self.class_name = tag_element.elements[0]
            else:
                assert False, 'Unknown tag element: %s' % tag_element

        self.type_decl = _create_sema_node(type_token)

    @property
    def type_name(self):
        return self.type_decl.type_name

    def reference_name(self):
        return self.type_decl.type_name

    def references(self):
        return self.type_decl.references()

    def __str__(self):
        class_spec = []
        if self.class_name:
            class_spec.append(self.class_name)
        class_spec.append(self.class_number)

        result = '[%s] ' % ' '.join(class_spec)
        if self.implicit:
            result += 'IMPLICIT '

        result += str(self.type_decl)

        return result

    __repr__ = __str__


class SimpleType(object):
    def __init__(self, elements):
        self.constraint = None
        self.type_name = elements[0]
        if len(elements) > 1 and elements[1].ty == 'Constraint':
            self.constraint = Constraint(elements[1].elements)

    def reference_name(self):
        return self.type_name

    def references(self):
        refs = [self.type_name]
        if self.constraint:
            refs.extend(self.constraint.references())

        return refs

    def __str__(self):
        if self.constraint is None:
            return self.type_name

        return '%s %s' % (self.type_name, self.constraint)

    __repr__ = __str__


class UserDefinedType(object):
    def __init__(self, elements):
        self.type_name = elements[0]

    def references(self):
        return [self.type_name]

    def __str__(self):
        return self.type_name

    __repr__ = __str__


class Constraint(object):
    def __init__(self, elements):
        min_value, max_value = elements

        self.min_value = _maybe_create_sema_node(min_value)
        self.max_value = _maybe_create_sema_node(max_value)

    def references(self):
        refs = []
        if isinstance(self.min_value, ValueReference):
            refs.append(self.min_value.reference_name())

        if isinstance(self.max_value, ValueReference):
            refs.append(self.max_value.reference_name())

        return refs

    def __str__(self):
        return '(%s..%s)' % (self.min_value, self.max_value)

    __repr__ = __str__


class ComponentType(object):
    def __init__(self, elements):
        first_token = elements[0]
        if first_token.ty == 'Type':
            # an unnamed member
            type_token = first_token
            self.identifier = _get_next_unnamed()
            elements = elements[1:]
        elif first_token.ty == 'Identifier':
            # an identifier
            self.identifier = first_token.elements[0]
            type_token = elements[1]
            elements = elements[2:]

        self.optional = elements and elements[0].ty == 'ComponentOptional'

        if elements and elements[0].ty == 'ComponentDefault':
            default_spec = elements[0]
            assert default_spec.elements[0] == 'DEFAULT'
            self.default_value = default_spec.elements[1]
        else:
            self.default_value = None

        self.type_decl = _create_sema_node(type_token)

    def references(self):
        # TODO: Value references in DEFAULT
        return self.type_decl.references()

    def __str__(self):
        result = '%s %s' % (self.identifier, self.type_decl)
        if self.optional:
            result += ' OPTIONAL'

        if not self.default_value is None:
            result += ' DEFAULT ' + self.default_value
        
        return result

    __repr__ = __str__


class NamedType(object):
    def __init__(self, elements):
        assert(elements[0].ty == 'Identifier')
        assert(elements[1].ty == 'Type')
        self.identifier = elements[0].elements[0]
        self.type_decl = _create_sema_node(elements[1])

    def references(self):
        return self.type_decl.references()

    def __str__(self):
        return '%s %s' % (self.identifier, self.type_decl)

    __repr__ = __str__


class ValueListType(object):
    def __init__(self, elements):
        self.type_name = elements[0]
        if len(elements) > 1:
            self.named_values = [_create_sema_node(token) for token in elements[1]]
        else:
            self.named_values = None

    def references(self):
        # TODO: Value references
        return []

    def __str__(self):
        if self.named_values:
            named_value_list = ', '.join(map(str, self.named_values))
            return '%s { %s }' % (self.type_name, named_value_list)
        else:
            return '%s' % self.type_name

    __repr__ = __str__


class BitStringType(object):
    def __init__(self, elements):
        self.type_name = elements[0]
        if len(elements) > 1:
            self.named_bits = [_create_sema_node(token) for token in elements[1]]
        else:
            self.named_bits = None

    def references(self):
        # TODO: Value references
        return []

    def __str__(self):
        if self.named_bits:
            named_bit_list = ', '.join(map(str, self.named_bits))
            return '%s { %s }' % (self.type_name, named_bit_list)
        else:
            return '%s' % self.type_name

    __repr__ = __str__


class NamedValue(object):
    def __init__(self, elements):
        identifier_token, value_token = elements
        self.identifier = identifier_token.elements[0]
        self.value = value_token.elements[0]

    def references(self):
        # TODO: This appears to never be called. Investigate.
        return []

    def __str__(self):
        return '%s (%s)' % (self.identifier, self.value)

    __repr__ = __str__


def _maybe_create_sema_node(token):
    if isinstance(token, parser.AnnotatedToken):
        return _create_sema_node(token)
    else:
        return token


def _create_sema_node(token):
    _assert_annotated_token(token)

    if token.ty == 'ModuleDefinition':
        return Module(token.elements)
    elif token.ty == 'TypeAssignment':
        return TypeAssignment(token.elements)
    elif token.ty == 'ValueAssignment':
        return ValueAssignment(token.elements)
    elif token.ty == 'ValueReference':
        return ValueReference(token.elements)
    elif token.ty == 'ComponentType':
        return ComponentType(token.elements)
    elif token.ty == 'NamedType':
        return NamedType(token.elements)
    elif token.ty == 'ValueListType':
        return ValueListType(token.elements)
    elif token.ty == 'BitStringType':
        return BitStringType(token.elements)
    elif token.ty == 'NamedValue':
        return NamedValue(token.elements)
    elif token.ty == 'Type':
        # Type tokens have a more specific type category
        # embedded as their first element
        return _create_sema_node(token.elements[0])
    elif token.ty == 'SimpleType':
        return SimpleType(token.elements)
    elif token.ty == 'ReferencedType':
        return UserDefinedType(token.elements)
    elif token.ty == 'TaggedType':
        return TaggedType(token.elements)
    elif token.ty == 'SequenceType':
        return SequenceType(token.elements)
    elif token.ty == 'ChoiceType':
        return ChoiceType(token.elements)
    elif token.ty == 'SequenceOfType':
        return SequenceOfType(token.elements)
    elif token.ty == 'SetOfType':
        return SetOfType(token.elements)

    raise Exception('Unknown token type: %s' % token.ty)


def _assert_annotated_token(obj):
    assert(type(obj) is parser.AnnotatedToken)


# HACK: Generate unique names for unnamed members
_unnamed_counter = 0
def _get_next_unnamed():
    global _unnamed_counter
    _unnamed_counter += 1
    return 'unnamed%d' % _unnamed_counter
