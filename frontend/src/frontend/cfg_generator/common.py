"""Common classes for generating benchmarks."""

import random
from typing import Any, List, Dict, Callable, Optional
from frontend.proto import cfg_pb2


class IDGenerator(object):
    """Returns an unused integer as a unique ID."""
    next_id = 0

    @staticmethod
    def next() -> int:
        """Get the next ID."""
        IDGenerator.next_id += 1
        return IDGenerator.next_id


# A type hint alias for function selector functions like pop_random_element.
FunctionSelector = Callable[[List[Any]], Any]


def pop_random_element(somelist: List[Any]) -> Any:
    """Pop off a random element from the list."""
    if not somelist:
        raise IndexError('pop_random_element: list is empty')
    idx = random.randrange(0, len(somelist))
    return somelist.pop(idx)


# The body of every function that we will generate. It simply performs a
# sequence of arithmetic operations. We write it in assembly to have better
# control over what the compiler does to it.
FUNCTION_BODY_XARCH: str = (
            'int x=1, y=0, z=0, w=0, tmp=0;\n'
            'for (int i = 0; i < ITERS; i++) {\n'
            '#ifdef __aarch64__\n'
            '  asm volatile (\n'
            '      "mul %0, %0, %0\\n\\t"\n'
            '      "mov %1, %0\\n\\t"\n'
            '      "add %1, %1, #0x3\\n\\t"\n'
            '      "mov %2, %1\\n\\t"\n'
            '      "mul %2, %2, %0\\n\\t"\n'
            '      "mov %4, #0x3039\\n\\t"\n'
            '      "add %2, %2, %4\\n\\t"\n'
            '      "mul %2, %2, %2\\n\\t"\n'
            '      "add %2, %2, %0\\n\\t"\n'
            '      "sub %2, %2, %1\\n\\t"\n'
            '      "mov %3, %2\\n\\t"\n'
            ': "=&r"(x), "=&r"(y), "=&r"(z), "=r"(w), "=r"(tmp)\n'
            ': "0"(x), "1"(y), "2"(z) : );\n'
            '#else\n'
            '  asm volatile (\n'
            '      "imul %0, %0\\n\\t"\n'
            '      "mov %0, %1\\n\\t"\n'
            '      "add $0x3, %1\\n\\t"\n'
            '      "mov %1, %2\\n\\t"\n'
            '      "imul %0, %2\\n\\t"\n'
            '      "add $0x3039, %2\\n\\t"\n'
            '      "imul %2, %2\\n\\t"\n'
            '      "add %0, %2\\n\\t"\n'
            '      "sub %1, %2\\n\\t"\n'
            '      "mov %2, %3\\n\\t"\n'
            ': "=&r"(x), "=&r"(y), "=&r"(z), "=r"(w)\n'
            ': "0"(x), "1"(y), "2"(z) : );\n'
            '#endif\n'
            '}')


class BaseGenerator(object):
    """Common functionality for generating benchmarks."""

    def __init__(self) -> None:
        # Map from code block body ID to the CodeBlockBody proto.
        self._code_block_bodies: Dict[int, cfg_pb2.CodeBlockBody] = {}
        # Map from code block ID to the CodeBlock proto.
        self._code_blocks: Dict[int, cfg_pb2.CodeBlock] = {}
        # Map from function ID to the function proto.
        self._functions: Dict[int, cfg_pb2.Function] = {}
        self._function_body: cfg_pb2.CodeBlockBody = self._add_code_block_body(
            FUNCTION_BODY_XARCH)

    def function_name(self, function_id: int) -> str:
        return 'function_%d' % function_id

    def _add_code_block_body(self, code: str = '') -> cfg_pb2.CodeBlockBody:
        next_id = IDGenerator.next()
        self._code_block_bodies[next_id] = cfg_pb2.CodeBlockBody(
            id=next_id, instructions=code)
        return self._code_block_bodies[next_id]

    def _add_code_block(self) -> cfg_pb2.CodeBlock:
        next_id = IDGenerator.next()
        self._code_blocks[next_id] = cfg_pb2.CodeBlock(id=next_id)
        return self._code_blocks[next_id]

    def _add_function_with_id(self, next_id: int) -> cfg_pb2.Function:
        if next_id in self._functions:
            raise KeyError('there already exists a function with id %d' %
                           next_id)
        self._functions[next_id] = self._create_function_with_signature(next_id)
        return self._functions[next_id]

    def _create_function_with_signature(self, next_id: int) -> cfg_pb2.Function:
        func = cfg_pb2.Function(id=next_id)
        signature = 'void %s' % self.function_name(next_id)
        sig_body = self._add_code_block_body(signature)
        func.signature.code_block_body_id = sig_body.id
        return func

    def _generate_cfg(self, functions: Dict[int, cfg_pb2.Function],
                      code_block_bodies: Dict[int, cfg_pb2.CodeBlockBody],
                      entry_func_id: int) -> cfg_pb2.CFG:
        cfg_proto = cfg_pb2.CFG()
        for func in functions.values():
            cfg_proto.functions.append(func)
        for cb in code_block_bodies.values():
            cfg_proto.code_block_bodies.append(cb)
        cfg_proto.entry_point_function = entry_func_id
        return cfg_proto

    def _add_code_prefetch_code_block(self,
                                      function_id: Optional[int] = None,
                                      code_block_id: Optional[int] = None,
                                      degree: int = 1) -> cfg_pb2.CodeBlock:
        """Create a code prefetch code block.

        The target address to prefetch is specified by either function_id or
        code_block_id, which also indicate what the target type is. Only one of
        these can be specified.
        """
        if function_id is not None and code_block_id is not None:
            raise ValueError(
                'cannot specify both function_id and code_block_id')
        if degree <= 0:
            raise ValueError('prefetch degree must be > 0')
        prefetch_inst = self._add_code_block_body()
        if function_id is not None:
            prefetch_inst.code_prefetch.type = \
                cfg_pb2.CodePrefetchInst.TargetType.FUNCTION
            prefetch_inst.code_prefetch.target_id = function_id
        elif code_block_id is not None:
            prefetch_inst.code_prefetch.type = \
                cfg_pb2.CodePrefetchInst.TargetType.CODE_BLOCK
            prefetch_inst.code_prefetch.target_id = code_block_id
        else:
            raise ValueError('must specify one of function_id or code_block_id')
        prefetch_inst.code_prefetch.degree = degree
        prefetch_block = self._add_code_block()
        prefetch_block.code_block_body_id = prefetch_inst.id
        return prefetch_block
