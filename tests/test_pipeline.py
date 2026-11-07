import asyncio
from dataclasses import FrozenInstanceError
import unittest

from hidden_moves import Moves
from hidden_moves.adapters import CapabilityCatalog
from moveset import DefinitionError, Input, Pipeline, PipelineError, Ref, Step, StepError


def catalog(**functions):
    moves = Moves()
    for name, function in functions.items():
        moves.learn(function, name=name)
    return CapabilityCatalog(moves, functions)


def normalize(text: str) -> str:
    return " ".join(text.lower().split())


def report(text: str) -> dict[str, str]:
    return {"text": text}


def definition():
    return Pipeline("text", version="1", steps=(
        Step("normalize", "normalize", {"text": Input("text")}),
        Step("report", "report", {"text": Ref("normalize")}),
    ), outputs={"report": Ref("report")})


class PipelineTests(unittest.TestCase):
    def test_reuses_ordinary_functions_and_preserves_scalar_records(self):
        bound = definition().bind(catalog(normalize=normalize, report=report), catalog_version="v1")
        self.assertEqual(normalize(" Hi THERE "), "hi there")
        result = bound.run({"text": " Hi THERE "})
        self.assertEqual(result.results, {"normalize": "hi there", "report": {"text": "hi there"}})
        self.assertEqual(result.outputs, {"report": {"text": "hi there"}})
        result.results["report"]["text"] = "changed"
        self.assertEqual(result.outputs["report"]["text"], "hi there")
        self.assertEqual(bound.run({"text": "x"}).outputs["report"]["text"], "x")

    def test_definition_is_deeply_immutable(self):
        arguments = {"text": "before"}
        step = Step("first", "normalize", arguments)
        pipeline = Pipeline("copy", version="1", steps=(step,), outputs={"result": Ref("first")})
        arguments["text"] = "after"
        step.arguments["text"] = "also after"
        pipeline.outputs["new"] = 2
        pipeline.manifest["steps"].clear()
        self.assertEqual(step.arguments["text"], "before")
        self.assertEqual(pipeline.outputs, {"result": Ref("first")})
        with self.assertRaises(FrozenInstanceError):
            step.id = "other"

    def test_static_errors_happen_without_invocation(self):
        calls = []

        def effect(text: str) -> str:
            calls.append(text)
            return text

        selected = catalog(effect=effect)
        for arguments in ({}, {"text": 1}, {"text": "x", "extra": Ref("first")}):
            with self.subTest(arguments=arguments), self.assertRaises(PipelineError):
                Pipeline("bad", version="1", steps=(Step("first", "effect", {"text": "x"}),
                    Step("second", "effect", arguments)), outputs={}).bind(selected, catalog_version="1")
        bound = Pipeline("inputs", version="1", steps=(Step("first", "effect", {"text": "x"}),
            Step("second", "effect", {"text": Input("missing")})), outputs={}).bind(selected, catalog_version="1")
        with self.assertRaises(StepError):
            bound.run({})
        with self.assertRaises(StepError):
            bound.run({"missing": False})
        self.assertEqual(calls, [])
        with self.assertRaises(StepError):
            definition().bind(selected, catalog_version="1")

    def test_names_references_and_limits(self):
        for name in ("", "a b", "a" * 129, True):
            with self.assertRaises(DefinitionError):
                Step(name, "normalize", {})
        for path in ((True,), (-1,), ["x"]):
            with self.assertRaises(DefinitionError):
                Ref("first", path)
        with self.assertRaises(DefinitionError):
            Pipeline("bad", version="1", steps=(Step("first", "normalize", {"text": Ref("later")}),), outputs={})
        with self.assertRaises(DefinitionError):
            Pipeline("bad", version="1", steps=(Step("same", "normalize", {}), Step("same", "normalize", {})), outputs={})
        for value in (float("nan"), object(), {1: "bad"}, "\ud800", "x" * 1_048_576):
            with self.subTest(value=type(value)), self.assertRaises(PipelineError):
                Step("bad", "normalize", {"text": value})

    def test_missing_path_and_invalid_reference_type_never_call_consumer(self):
        calls = []

        def producer() -> dict[str, list[int]]:
            calls.append("producer")
            return {"items": [3]}

        def consume(text: str) -> str:
            calls.append("consumer")
            return text

        for ref in (Ref("first")["missing"], Ref("first")["items"][0], Ref("first")[0]):
            calls.clear()
            bound = Pipeline("paths", version="1", steps=(Step("first", "producer", {}),
                Step("second", "consume", {"text": ref})), outputs={}).bind(catalog(producer=producer, consume=consume), catalog_version="1")
            with self.assertRaises(StepError) as caught:
                bound.run({})
            self.assertEqual(caught.exception.step_id, "second")
            self.assertEqual(calls, ["producer"])

    def test_nested_mapping_copy_and_input_paths(self):
        def mutate(items: list[dict[str, int]]) -> list[dict[str, int]]:
            items[0]["x"] += 1
            return items

        inputs = {"payload": {"x": 2}}
        bound = Pipeline("nested", version="1", steps=(Step("one", "mutate", {"items": [Input("payload")]}),
            Step("two", "mutate", {"items": Ref("one")})), outputs={"first": Ref("one")[0]["x"], "second": Ref("two")}).bind(catalog(mutate=mutate), catalog_version="1")
        result = bound.run(inputs)
        self.assertEqual(result.outputs, {"first": 3, "second": [{"x": 4}]})
        self.assertEqual(inputs, {"payload": {"x": 2}})

    def test_failures_keep_context_and_cause(self):
        def fail() -> str:
            raise RuntimeError("failed")

        bound = Pipeline("failure", version="1", steps=(Step("one", "fail", {}),), outputs={}).bind(catalog(fail=fail), catalog_version="1")
        with self.assertRaises(StepError) as caught:
            bound.run({})
        self.assertIsInstance(caught.exception.__cause__, RuntimeError)

    def test_fingerprint_covers_definition_mapping_schemas_and_catalog_version(self):
        selected = catalog(normalize=normalize, report=report)
        bound = definition().bind(selected, catalog_version="v1")
        self.assertEqual(bound.fingerprint, definition().bind(selected, catalog_version="v1").fingerprint)
        self.assertNotEqual(bound.fingerprint, definition().bind(selected, catalog_version="v2").fingerprint)
        bound.manifest.clear()
        self.assertEqual(len(bound.fingerprint), 64)


class AsyncTests(unittest.IsolatedAsyncioTestCase):
    async def test_mixed_order_and_sync_preflight(self):
        calls = []

        def first(text: str) -> str:
            calls.append("first")
            return text

        async def second(text: str) -> str:
            await asyncio.sleep(0)
            calls.append("second")
            return text.upper()

        bound = Pipeline("mixed", version="1", steps=(Step("a", "first", {"text": "hello"}),
            Step("b", "second", {"text": Ref("a")})), outputs={"text": Ref("b")}).bind(catalog(first=first, second=second), catalog_version="1")
        with self.assertRaises(DefinitionError):
            bound.run({})
        self.assertEqual(calls, [])
        self.assertEqual((await bound.arun({})).outputs, {"text": "HELLO"})
        self.assertEqual(calls, ["first", "second"])

    async def test_cancellation_propagates(self):
        async def stop() -> str:
            raise asyncio.CancelledError

        bound = Pipeline("cancel", version="1", steps=(Step("one", "stop", {}),), outputs={}).bind(catalog(stop=stop), catalog_version="1")
        with self.assertRaises(asyncio.CancelledError):
            await bound.arun({})
