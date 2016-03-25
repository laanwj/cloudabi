# Copyright (c) 2016 Nuxi (https://nuxi.nl/) and contributors.
#
# This file is distributed under a 2-clause BSD license.
# See the LICENSE and CONTRIBUTORS files for details.

from .abi import *
from .generator import *


class CNaming:

    def __init__(self, prefix, md_prefix=None, c11=True):
        self.prefix = prefix
        self.md_prefix = md_prefix
        self.c11 = c11

    def typename(self, type):
        if isinstance(type, VoidType):
            return 'void'
        elif isinstance(type, IntType):
            if type.name == 'char':
                return 'char'
            return '{}_t'.format(type.name)
        elif isinstance(type, UserDefinedType):
            prefix = self.prefix
            if self.md_prefix is not None and type.layout.machine_dep:
                prefix = self.md_prefix
            return '{}{}_t'.format(prefix, type.name)
        elif isinstance(type, AtomicType):
            if self.c11:
                return '_Atomic({})'.format(
                    self.typename(type.target_type))
            else:
                return self.typename(type.target_type)
        elif isinstance(type, PointerType) or isinstance(type, ArrayType):
            return self.vardecl(type, '')
        else:
            raise Exception('Unable to generate C declaration '
                            'for type: {}'.format(type))

    def valname(self, type, value):
        return '{}{}{}'.format(self.prefix, type.cprefix, value.name).upper()

    def syscallname(self, syscall):
        prefix = self.prefix
        if self.md_prefix is not None and syscall.machine_dep:
            prefix = self.md_prefix
        return '{}sys_{}'.format(prefix, syscall.name)

    def vardecl(self, type, name, array_need_parens=False):
        if isinstance(type, PointerType):
            decl = self.vardecl(type.target_type, '*{}'.format(name),
                                array_need_parens=True)
            if type.const:
                decl = 'const ' + decl
            return decl
        elif isinstance(type, ArrayType):
            if array_need_parens:
                name = '({})'.format(name)
            return self.vardecl(
                type.element_type, '{}[{}]'.format(
                    name, type.count))
        else:
            return '{} {}'.format(self.typename(type), name)


class CGenerator(Generator):

    def __init__(self, naming, header_guard=None, machine_dep=None,
                 md_type=None, preamble=''):
        super().__init__(comment_prefix='// ')
        self.naming = naming
        self.header_guard = header_guard
        self.machine_dep = machine_dep
        self.md_type = md_type
        self.preamble = preamble

    def generate_head(self, abi):
        super().generate_head(abi)
        if self.header_guard is not None:
            print('#ifndef {}'.format(self.header_guard))
            print('#define {}'.format(self.header_guard))
            print()
            print(self.preamble)

    def generate_foot(self, abi):
        if self.header_guard is not None:
            print('#endif')
        super().generate_foot(abi)

    def mi_type(self, mtype):
        if self.md_type is not None:
            if isinstance(mtype, PointerType) or mtype.name == 'size':
                return self.md_type
            elif isinstance(mtype, ArrayType):
                return ArrayType(mtype.count, self.mi_type(mtype.element_type))
            elif isinstance(mtype, AtomicType):
                return AtomicType(self.mi_type(mtype.target_type))
        return mtype


class CSyscalldefsGenerator(CGenerator):

    def generate_struct_members(self, abi, type, indent=''):
        for m in type.raw_members:
            if isinstance(m, SimpleStructMember):
                mtype = self.mi_type(m.type)
                if mtype.layout.align[0] == mtype.layout.align[1]:
                    alignas = '_Alignas({}) '.format(mtype.layout.align[0])
                else:
                    alignas = ''
                print('{}{}{};'.format(
                    indent, alignas, self.naming.vardecl(mtype, m.name)))
            elif isinstance(m, VariantStructMember):
                print('{}union {{'.format(indent))
                for x in m.members:
                    if x.name is None:
                        self.generate_struct_members(
                            abi, x.type, indent + '\t')
                    else:
                        print('{}\tstruct {{'.format(indent))
                        self.generate_struct_members(
                            abi, x.type, indent + '\t\t')
                        print('{}\t}} {};'.format(indent, x.name))
                print('{}}};'.format(indent))
            else:
                raise Exception('Unknown struct member: {}'.format(m))

    def generate_type(self, abi, type):

        if self.machine_dep is not None:
            if type.layout.machine_dep != self.machine_dep:
                return

        if isinstance(type, IntLikeType):
            print('typedef {};'.format(self.naming.vardecl(
                type.int_type, self.naming.typename(type))))
            if len(type.values) > 0:
                width = max(
                    len(self.naming.valname(type, v)) for v in type.values)
                if (isinstance(type, FlagsType) or
                        isinstance(type, OpaqueType)):
                    if len(type.values) == 1 and type.values[0].value == 0:
                        val_format = 'd'
                    else:
                        val_format = '#0{}x'.format(
                            type.layout.size[0] * 2 + 2)
                else:
                    val_width = max(len(str(v.value)) for v in type.values)
                    val_format = '{}d'.format(val_width)

                for v in type.values:
                    print('#define {name:{width}} '
                          '{val:{val_format}}'.format(
                              name=self.naming.valname(type, v),
                              width=width,
                              val=v.value,
                              val_format=val_format))

        elif isinstance(type, FunctionType):
            parameters = []
            for p in type.parameters.raw_members:
                parameters.append(self.naming.vardecl(
                    self.mi_type(p.type), p.name))
            print('typedef {};'.format(
                self.naming.vardecl(
                    self.mi_type(type.return_type),
                    '{}({})'.format(self.naming.typename(type),
                                    ', '.join(parameters)),
                    array_need_parens=True)))

        elif isinstance(type, StructType):
            typename = self.naming.typename(type)

            print('typedef struct {')
            self.generate_struct_members(abi, type, '\t')
            print('}} {};'.format(typename))

            self.generate_offset_asserts(typename, type.raw_members)
            self.generate_size_assert(typename, type.layout.size)
            self.generate_align_assert(typename, type.layout.align)

        else:
            raise Exception('Unknown class of type: {}'.format(type))

        print()

    def generate_offset_asserts(
            self, type_name, members, prefix='', offset=(0, 0)):
        for m in members:
            if isinstance(m, VariantMember):
                mprefix = prefix
                if m.name is not None:
                    mprefix += m.name + '.'
                self.generate_offset_asserts(
                    type_name, m.type.members, mprefix, offset)
            elif m.offset is not None:
                moffset = (offset[0] + m.offset[0], offset[1] + m.offset[1])
                if isinstance(m, VariantStructMember):
                    self.generate_offset_asserts(
                        type_name, m.members, prefix, moffset)
                else:
                    self.generate_offset_assert(
                        type_name, prefix + m.name, moffset)

    def generate_offset_assert(self, type_name, member_name, offset):
        self.generate_layout_assert(
            'offsetof({}, {})'.format(type_name, member_name), offset)

    def generate_size_assert(self, type_name, size):
        self.generate_layout_assert('sizeof({})'.format(type_name), size)

    def generate_align_assert(self, type_name, align):
        self.generate_layout_assert('_Alignof({})'.format(type_name), align)

    def generate_layout_assert(self, expression, value):
        static_assert = '_Static_assert({}, "Incorrect layout");'
        if value[0] == value[1] or (
                self.md_type is not None and
                self.md_type.layout.size in ((4, 4), (8, 8))):
            v = value[1]
            if self.md_type is not None and self.md_type.layout.size == (4, 4):
                v = value[0]
            print(static_assert.format('{} == {}'.format(expression, v)))
        else:
            voidptr = self.naming.typename(PointerType())
            print(static_assert.format('sizeof({}) != 4 || {} == {}'.format(
                voidptr, expression, value[0])))
            print(static_assert.format('sizeof({}) != 8 || {} == {}'.format(
                voidptr, expression, value[1])))

    def generate_syscalls(self, abi, syscalls):
        pass


class CSyscallsGenerator(CGenerator):

    def syscall_params(self, syscall):
        params = []
        for p in syscall.input.raw_members:
            params.append(self.naming.vardecl(p.type, p.name))
        for p in syscall.output.raw_members:
            params.append(self.naming.vardecl(PointerType(p.type), p.name))
        return params

    def generate_syscall(self, abi, syscall):
        if syscall.noreturn:
            noreturn = '_Noreturn '
            return_type = VoidType()
        else:
            noreturn = ''
            return_type = UserDefinedType('errno')
        print('static inline {}{}'.format(
            noreturn, self.naming.typename(return_type)))
        print('{}('.format(self.naming.syscallname(syscall)), end='')
        params = self.syscall_params(syscall)
        if params == []:
            print('void', end='')
        else:
            print()
            for p in params[:-1]:
                print('\t{},'.format(p))
            print('\t{}'.format(params[-1]))
        print(')', end='')
        self.generate_syscall_body(abi, syscall)
        print()

    def generate_syscall_body(self, abi, syscall):
        print(';')

    def generate_types(self, abi, types):
        pass


class CSyscallsImplGenerator(CSyscallsGenerator):

    def generate_syscall_body(self, abi, syscall):
        print(' {')

        check_okay = len(syscall.output.raw_members) > 0

        defined_regs = set()

        def define_reg(register, value=None):
            if value is None:
                if register in defined_regs:
                    return
                defn = ''
            else:
                assert(register not in defined_regs)
                defn = ' = {}'.format(value)
            print('\tregister {decl} asm("{reg}"){defn};'.format(
                decl=self.naming.vardecl(self.register_t,
                                         'reg_{}'.format(register)),
                reg=register, defn=defn))
            defined_regs.add(register)

        define_reg(self.syscall_num_register, syscall.number)

        for i, p in enumerate(syscall.input.raw_members):
            define_reg(self.input_registers[i], self._ccast(
                p.type, self.register_t, p.name))

        for i in range(len(syscall.output.raw_members)):
            define_reg(self.output_registers[i])

        define_reg(self.errno_register)

        if check_okay:
            print('\tregister {};'.format(
                self.naming.vardecl(self.okay_t, 'okay')))

        print('\tasm volatile (')
        print(self.asm)
        if check_okay:
            print(self.asm_check)

        first = True
        if check_okay:
            print('\t\t: "=r"(okay)')
            first = False
        for i in range(len(syscall.output.raw_members)):
            print('\t\t{} "=r"(reg_{})'.format(
                ':' if first else ',', self.output_registers[i]))
            first = False
        if not syscall.noreturn:
            if (self.errno_register not in
                    self.output_registers[:len(syscall.output.raw_members)]):
                print('\t\t{} "=r"(reg_{})'.format(
                    ':' if first else ',', self.errno_register))
                first = False
        if first:
            print('\t\t:')

        print('\t\t: "r"(reg_{})'.format(self.syscall_num_register))
        for i in range(len(syscall.input.raw_members)):
            print('\t\t, "r"(reg_{})'.format(self.input_registers[i]))

        print('\t\t: {});'.format(self.clobbers))
        if check_okay:
            print('\tif (okay) {')
            for i, p in enumerate(syscall.output.raw_members):
                print('\t\t*{} = {};'.format(p.name, self._ccast(
                    self.register_t,
                    p.type,
                    "reg_{}".format(self.output_registers[i]))))
            print('\t\treturn 0;')
            print('\t}')

        if syscall.noreturn:
            print('\tfor (;;);')
        else:
            print('\treturn reg_{};'.format(self.errno_register))

        print('}')

    def _ccast(self, type_from, type_to, name):
        if (isinstance(type_from, StructType) or
                isinstance(type_to, StructType)):
            if type_from.layout.size != type_to.layout.size:
                raise Exception('Can\'t cast {} to {}.'.format(
                    type_from, type_to))
            return '*({})&{}'.format(
                self.naming.typename(PointerType(type_to)), name)
        else:
            return '({}){}'.format(self.naming.typename(type_to), name)


class CSyscallsX86_64Generator(CSyscallsImplGenerator):

    syscall_num_register = 'rax'
    input_registers = ['rdi', 'rsi', 'rdx', 'r10', 'r8', 'r9']
    output_registers = ['rax', 'rdx']
    errno_register = 'rax'

    clobbers = '"memory", "rcx", "rdx", "r8", "r9", "r10", "r11"'

    register_t = int_types['uint64']
    okay_t = int_types['char']

    asm = '\t\t"\\tsyscall\\n"'
    asm_check = '\t\t"\\tsetnc %0\\n"'


class CSyscallsAarch64Generator(CSyscallsImplGenerator):

    syscall_num_register = 'x8'
    input_registers = ['x0', 'x1', 'x2', 'x3', 'x4', 'x5']
    output_registers = ['x0', 'x1']
    errno_register = 'x0'

    output_register_start = 1

    clobbers = ('"memory"\n'
                '\t\t, "x0", "x1", "x2", "x3", "x4", "x5", "x6", "x7"\n'
                '\t\t, "x8", "x9", "x10", "x11", "x12", "x13", "x14", "x15"\n'
                '\t\t, "x16", "x17", "x18"\n'
                '\t\t, "d0", "d1", "d2", "d3", "d4", "d5", "d6", "d7"')

    register_t = int_types['uint64']
    okay_t = register_t

    asm = '\t\t"\\tsvc 0\\n"'
    asm_check = '\t\t"\\tcset %0, cc\\n"'