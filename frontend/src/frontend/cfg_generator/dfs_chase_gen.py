"""Generates a DFS tree-based instruction pointer chase benchmark.

The benchmark consists of a full binary tree of depth D. Each node is a
conditional branch that calls one child function. This process repeats until we
reach the leaf.
"""

from __future__ import annotations
from typing import List, Optional, Dict
from frontend.proto import cfg_pb2
from frontend.cfg_generator import common

MODULE_NAME = 'dfs_chase_gen'


def register_args(parser):
    subparser = parser.add_parser(MODULE_NAME)
    subparser.add_argument('--depth',
                           default=20,
                           type=int,
                           help='Depth of the function call tree.')
    subparser.add_argument(
        '--use_indirect_calls',
        default=False,
        action='store_true',
        help='If true, uses indirect calls to call functions')
    subparser.add_argument('--branch_probability',
                           default=0.5,
                           type=float,
                           help='Branch taken probability.')
    subparser.add_argument('--insert_code_prefetches',
                           default=False,
                           action='store_true',
                           help='Insert code prefetches into the '
                           'callchains. Not available on all platforms.')


class DFSChaseGenerator(common.BaseGenerator):
    """Generates a DFS instruction pointer chase benchmark."""

    def __init__(self, depth: int, use_indirect_calls: bool,
                 left_path_probability: float,
                 insert_code_prefetches: bool) -> None:
        """Constructs a DFS pointer chase generator.

        Args:
            depth: The depth of the binary tree.
            use_indirect_calls: Use indirect calls to traverse the tree. If
                false, the CFG will create conditional branches + direct calls.
            left_path_probability: The probability of taking the left path.
        """
        super().__init__()

        self._depth: int = depth
        # Map from function id to its left/right callee.
        self._function_tree: Dict[int, List[int]] = {}
        # A list of functions that do not call other functions.
        self._leaf_functions: List[int] = []
        # ID of the function at the root of the function tree.
        self._root_func: int = 0
        self._insert_code_prefetches: bool = insert_code_prefetches
        self._left_path_probability: float = left_path_probability
        self._use_indirect_calls: bool = use_indirect_calls
        self._function_body: cfg_pb2.CodeBlockBody = self._add_code_block_body(
            'int x = 1; asm volatile ("" : : "r" (x));\n'
            'int y = x*x + 3; asm volatile ("" : : "r" (y));\n'
            'int z = y*x + 12345; asm volatile ("" : : "r" (z));\n'
            'int w = z*z + x - y; asm volatile ("" : : "r"(w));\n')

    def _add_code_block_with_branch(
            self,
            branch_type: cfg_pb2.Branch.BranchTypeValue,
            target: Optional[int] = None,
            probability: Optional[float] = None) -> cfg_pb2.CodeBlock:
        """Add an empty code block with the specified terminator branch."""
        block = self._add_code_block()
        block.terminator_branch.type = branch_type
        if target:
            block.terminator_branch.targets.append(target)
        if probability:
            block.terminator_branch.taken_probability.append(probability)
        return block

    def _generate_indirect_call_code_blocks(
            self, call_targets: List[int],
            callee_probability: float) -> List[cfg_pb2.CodeBlock]:
        """Generates a single CodeBlock that indirectly calls 2 targets.

        Since we're using indirect calls here, we don't need to encode
        conditional branching into the CFG. We allow the backend to
        automatically determine which of the call targets are called.

        Args:
            call_targets: List of Function IDs. Should be of length 2.
            callee_probability: Probability that we call the first callee in
               call_targets.
        Returns:
            One CodeBlock.
        """
        if len(call_targets) != 2:
            raise ValueError('call_targets must have length 2, got %d' %
                             len(call_targets))
        code_blocks: List[cfg_pb2.CodeBlock] = []

        # We don't know which target we're going to call, so prefetch all of
        # them.
        if self._insert_code_prefetches:
            for target in call_targets:
                code_blocks.append(
                    self._add_code_prefetch_code_block(function_id=target))

        call_block = self._add_code_block()
        call_block.terminator_branch.type = \
            cfg_pb2.Branch.BranchType.INDIRECT_CALL
        for target in call_targets:
            call_block.terminator_branch.targets.append(target)
        call_block.terminator_branch.taken_probability.append(
            callee_probability)
        call_block.terminator_branch.taken_probability.append(
            1.0 - callee_probability)
        call_block.code_block_body_id = self._function_body.id
        code_blocks.append(call_block)

        return code_blocks

    def _generate_conditional_branch_code_blocks(
            self, call_targets: List[int],
            probability: float) -> List[cfg_pb2.CodeBlock]:
        """Generates CodeBlocks to conditionally directly call two callees.

        The CFG directly encodes conditional branches with the given branch
        probabilities which lead to direct calls to the callees.
        """
        if len(call_targets) != 2:
            raise ValueError('call_targets must have length 2, got %d' %
                             len(call_targets))
        code_blocks: List[cfg_pb2.CodeBlock] = []

        # We have a few options for where to put the code prefetch, but we have
        # to execute it before the function body. So we either:
        #   1. Prefetch both, knowing that only one will be useful.
        #   2. Duplicate the function body in both sides of the branch.
        # Prefetching both is easier, and also acts as a model for more
        # realistic scenarios in which we have to prefetch far in advance of
        # knowing the control flow for sure.
        if self._insert_code_prefetches:
            code_blocks.append(
                self._add_code_prefetch_code_block(function_id=call_targets[0]))
            code_blocks.append(
                self._add_code_prefetch_code_block(function_id=call_targets[1]))

        # Conditional branch taken path.
        taken_block = self._add_code_block_with_branch(
            cfg_pb2.Branch.BranchType.DIRECT_CALL, call_targets[0], 1)
        taken_block_ret = self._add_code_block_with_branch(
            cfg_pb2.Branch.BranchType.RETURN)

        # Fallthrough block.
        ft_block = self._add_code_block_with_branch(
            cfg_pb2.Branch.BranchType.DIRECT_CALL, call_targets[1], 1)
        ft_block_ret = self._add_code_block_with_branch(
            cfg_pb2.Branch.BranchType.RETURN)

        cond_block = self._add_code_block_with_branch(
            cfg_pb2.Branch.BranchType.CONDITIONAL_DIRECT, taken_block.id,
            probability)
        cond_block.code_block_body_id = self._function_body.id

        code_blocks.append(cond_block)
        # Fallthrough must come right after the conditional branch.
        code_blocks.append(ft_block)
        code_blocks.append(ft_block_ret)
        code_blocks.append(taken_block)
        code_blocks.append(taken_block_ret)
        return code_blocks

    def _generate_leaf_function_code_blocks(self) -> cfg_pb2.CodeBlock:
        codeblock = self._add_code_block()
        codeblock.code_block_body_id = self._function_body.id
        codeblock.terminator_branch.type = cfg_pb2.Branch.BranchType.RETURN
        return codeblock

    def _generate_function_tree(self) -> None:
        next_id = common.IDGenerator.next()
        self._root_func = next_id
        queue = [next_id]
        for _ in range(0, self._depth - 1):
            children: List[int] = []
            for func in queue:
                self._function_tree[func] = [
                    common.IDGenerator.next(),
                    common.IDGenerator.next()
                ]
                children.extend(self._function_tree[func])
            queue = children
            # The callees of the second-to-last level in the tree are leaves.
            self._leaf_functions = children

    def _generate_functions(self) -> None:
        self._add_function_with_id(self._root_func)
        for caller, callees in self._function_tree.items():
            for callee in callees:
                self._add_function_with_id(callee)
            if self._use_indirect_calls:
                self._functions[caller].instructions.extend(
                    self._generate_indirect_call_code_blocks(
                        callees, self._left_path_probability))
            else:
                self._functions[caller].instructions.extend(
                    self._generate_conditional_branch_code_blocks(
                        callees, self._left_path_probability))

        for leaf in self._leaf_functions:
            self._functions[leaf].instructions.append(
                self._generate_leaf_function_code_blocks())

    def generate_cfg(self) -> cfg_pb2.CFG:
        self._generate_function_tree()
        self._generate_functions()
        return self._generate_cfg(self._functions, self._code_block_bodies,
                                  self._root_func)


def generate_cfg(args):
    """Generate a CFG of arbitrary callchains."""
    print('Generating DFS instruction pointer chase benchmark...')
    generator = DFSChaseGenerator(args.depth, args.use_indirect_calls,
                                  args.branch_probability,
                                  args.insert_code_prefetches)
    return generator.generate_cfg()
