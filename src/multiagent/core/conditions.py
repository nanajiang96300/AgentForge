"""Unified condition expression evaluator using ast module (no eval())."""
import ast


class ConditionSyntaxError(Exception):
    """Condition expression syntax error with position info."""
    def __init__(self, message: str, line: int = 0, col: int = 0):
        self.line = line
        self.col = col
        super().__init__(f"{message} (line {line}, col {col})" if line else message)


_OPS = {
    ast.Eq: lambda a, b: a == b,
    ast.NotEq: lambda a, b: a != b,
    ast.Gt: lambda a, b: a > b,
    ast.Lt: lambda a, b: a < b,
    ast.GtE: lambda a, b: a >= b,
    ast.LtE: lambda a, b: a <= b,
}


class _Evaluator(ast.NodeVisitor):
    """Walk AST and evaluate expression against context."""
    def __init__(self, context: dict):
        self._ctx = context
        self._stack = []

    def push(self, value):
        self._stack.append(value)

    def pop(self):
        return self._stack.pop()

    def visit_Compare(self, node):
        self.generic_visit(node)
        right = self.pop()
        left = self.pop()
        for op_node in node.ops:
            if isinstance(op_node, ast.In):
                self.push(left in right)
            elif isinstance(op_node, ast.NotIn):
                self.push(left not in right)
            else:
                op_func = _OPS.get(type(op_node))
                if op_func is None:
                    raise ConditionSyntaxError(
                        f"Unsupported operator: {type(op_node).__name__}"
                    )
                self.push(op_func(left, right))

    def visit_Name(self, node):
        if node.id in self._ctx:
            self.push(self._ctx[node.id])
        elif node.id in ('True', 'False'):
            self.push(node.id == 'True')
        else:
            self.push(None)

    def visit_Constant(self, node):
        self.push(node.value)

    def visit_List(self, node):
        self.push([self._constant_value(e) for e in node.elts])

    def visit_Tuple(self, node):
        self.push(tuple(self._constant_value(e) for e in node.elts))

    def visit_BoolOp(self, node):
        for v in node.values:
            self.visit(v)
        values = [self.pop() for _ in node.values]
        if isinstance(node.op, ast.And):
            self.push(all(values))
        elif isinstance(node.op, ast.Or):
            self.push(any(values))

    def visit_UnaryOp(self, node):
        self.visit(node.operand)
        val = self.pop()
        if isinstance(node.op, ast.Not):
            self.push(not val)

    def _constant_value(self, node):
        if isinstance(node, ast.Constant):
            return node.value
        raise ConditionSyntaxError("Only constants allowed in lists")


class ConditionEvaluator:
    """Safely evaluate condition expressions against a context dict."""

    def evaluate(self, condition: str, context: dict) -> bool:
        if not condition or not condition.strip():
            return True
        try:
            tree = ast.parse(condition.strip(), mode='eval')
        except SyntaxError as e:
            raise ConditionSyntaxError(str(e), e.lineno or 0, e.offset or 0)
        try:
            visitor = _Evaluator(context)
            visitor.visit(tree.body)
            return bool(visitor.pop())
        except ConditionSyntaxError:
            raise
        except Exception as e:
            raise ConditionSyntaxError(f"Evaluation failed: {e}")

    def validate(self, condition: str) -> tuple:
        if not condition or not condition.strip():
            return True, None
        try:
            ast.parse(condition.strip(), mode='eval')
        except SyntaxError as e:
            return False, str(e)
        return True, None
