"""Every test of the engine runs.

The factory runs `python3 -m unittest discover -s engine -t .`. A class with `test…` methods that is not
a `unittest.TestCase`, a file of tests whose name does not start with `test`, or a test method hidden by a
later one of the same name drops out of that run without a word, and the run stays green."""
from __future__ import annotations

import ast
import importlib
import types
import unittest
from pathlib import Path

ENGINE = Path(__file__).resolve().parents[2]
ROOT = ENGINE.parent


def sources_of_tests() -> list[Path]:
    return sorted(p for p in ENGINE.rglob("*.py") if "tests" in p.relative_to(ENGINE).parts[:-1])


def classes_with_tests(tree: ast.Module) -> dict[str, list[str]]:
    """Module-level classes that define `test…` methods: name → the method names, in order."""
    out = {}
    for node in tree.body:
        if isinstance(node, ast.ClassDef):
            names = [m.name for m in node.body
                     if isinstance(m, (ast.FunctionDef, ast.AsyncFunctionDef)) and m.name.startswith("test")]
            if names:
                out[node.name] = names
    return out


def not_test_cases(module: types.ModuleType, tree: ast.Module) -> list[str]:
    """The classes of `module` with `test…` methods that unittest would not run."""
    return [name for name in classes_with_tests(tree)
            if not (isinstance(getattr(module, name, None), type) and issubclass(getattr(module, name), unittest.TestCase))]


class EveryTestRuns(unittest.TestCase):
    def test_every_file_with_tests_is_collected(self):
        stray = [str(p.relative_to(ROOT)) for p in sources_of_tests()
                 if not p.name.startswith("test") and classes_with_tests(ast.parse(p.read_text(encoding="utf-8")))]
        self.assertEqual(stray, [])

    def test_every_class_with_tests_is_a_test_case(self):
        lost = []
        for path in sources_of_tests():
            if not path.name.startswith("test"):
                continue
            module = importlib.import_module(".".join(path.relative_to(ROOT).with_suffix("").parts))
            lost += [f"{path.relative_to(ROOT)}: {name}"
                     for name in not_test_cases(module, ast.parse(path.read_text(encoding="utf-8")))]
        self.assertEqual(lost, [])

    def test_no_test_method_is_hidden_by_another_of_its_name(self):
        hidden = []
        for path in sources_of_tests():
            for cls, names in classes_with_tests(ast.parse(path.read_text(encoding="utf-8"))).items():
                hidden += [f"{path.relative_to(ROOT)}: {cls}.{n}" for n in sorted(set(names)) if names.count(n) > 1]
        self.assertEqual(hidden, [])

    def test_the_guard_sees_a_class_that_lost_its_base(self):
        source = ("import unittest\n"
                  "class Kept(unittest.TestCase):\n    def test_a(self):\n        pass\n"
                  "class Lost:\n    def test_b(self):\n        pass\n"
                  "class Helper:\n    def run(self):\n        pass\n")
        module = types.ModuleType("probe")
        exec(compile(source, "probe.py", "exec"), module.__dict__)
        self.assertEqual(not_test_cases(module, ast.parse(source)), ["Lost"])

    def test_the_guard_sees_a_hidden_method(self):
        source = "class T:\n    def test_a(self):\n        pass\n    def test_a(self):\n        pass\n"
        self.assertEqual(classes_with_tests(ast.parse(source)), {"T": ["test_a", "test_a"]})


if __name__ == "__main__":
    unittest.main()
