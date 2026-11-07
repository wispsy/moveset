import asyncio
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest

from func_seq import Control, DefinitionMismatchError, RecoveryRequiredError, StepFailedError
from hidden_moves import Moves
from hidden_moves.adapters import CapabilityCatalog
from moveset import Input, Pipeline, Ref, Step
from moveset.sequence import compile_script


def bind(function, *, value=None, version="1"):
    moves = Moves()
    moves.learn(function, name="operation")
    return Pipeline("sequence", version="1", steps=(
        Step("first", "operation", {"value": Input("value")}),
        Step("second", "operation", {"value": Ref("first") if value is None else value}),
    ), outputs={"value": Ref("second")}).bind(CapabilityCatalog(moves, ["operation"]), catalog_version=version)


class LifecycleTests(unittest.TestCase):
    def test_fresh_process_resume_and_identity(self):
        with tempfile.TemporaryDirectory() as directory:
            checkpoint, receipt = str(Path(directory) / "checkpoint.json"), str(Path(directory) / "receipt.txt")
            def run(mode, version="v1"):
                return subprocess.run([sys.executable, "-I", str(Path(__file__).with_name("worker.py")), checkpoint, receipt, mode, version], capture_output=True, text=True)
            first = run("pause")
            self.assertEqual(first.returncode, 0, first.stderr)
            self.assertIn('"paused"', first.stdout)
            for mode, version in (("input", "v1"), ("run", "v2")):
                mismatch = run(mode, version)
                self.assertNotEqual(mismatch.returncode, 0)
                self.assertIn("DefinitionMismatchError", mismatch.stderr)
            resumed = run("run")
            self.assertEqual(resumed.returncode, 0, resumed.stderr)
            self.assertEqual(Path(receipt).read_text().splitlines(), ["hello", "hello"])
            self.assertEqual(run("run").returncode, 0)
            self.assertEqual(Path(receipt).read_text().splitlines(), ["hello", "hello"])

    def test_mapping_mismatch_pause_retry_restart_and_outputs(self):
        calls = []

        def operation(value: str) -> str:
            calls.append(value)
            return value

        bound = bind(operation)
        with tempfile.TemporaryDirectory() as directory:
            checkpoint = Path(directory) / "checkpoint.json"
            control = Control()
            def observer(event):
                if event.kind == "finished":
                    control.request_pause()
            first = compile_script(bound, {"value": "hi"}).run(checkpoint=checkpoint, control=control, observer=observer)
            self.assertEqual(first.status, "paused")
            with self.assertRaises(DefinitionMismatchError):
                compile_script(bind(operation, value="different"), {"value": "hi"}).run(checkpoint=checkpoint)
            result = compile_script(bound, {"value": "hi"}).run(checkpoint=checkpoint)
            self.assertEqual(bound.finish({"value": "hi"}, result.results).outputs, {"value": "hi"})
            self.assertEqual(calls, ["hi", "hi"])
            compile_script(bound, {"value": "hi"}).run(checkpoint=checkpoint, restart=True)
            self.assertEqual(len(calls), 4)

    def test_failed_effect_requires_explicit_retry(self):
        calls = []

        def effect(value: str) -> str:
            calls.append(value)
            if len(calls) == 1:
                raise RuntimeError("after effect")
            return value

        script = compile_script(bind(effect), {"value": "x"})
        with self.assertRaises(StepFailedError):
            script.run()
        with self.assertRaises(RecoveryRequiredError):
            script.run()
        self.assertEqual(len(calls), 1)
        self.assertEqual(script.run(retry=True).status, "completed")
        self.assertEqual(len(calls), 3)


class AsyncLifecycleTests(unittest.IsolatedAsyncioTestCase):
    async def test_mixed_async_and_cancellation_uncertainty(self):
        calls = []

        def first(value: str) -> str:
            calls.append("sync")
            return value

        async def second(value: str) -> str:
            await asyncio.sleep(0)
            calls.append("async")
            return value.upper()

        moves = Moves()
        moves.learn(first)
        moves.learn(second)
        bound = Pipeline("mixed", version="1", steps=(Step("a", "first", {"value": "hello"}),
            Step("b", "second", {"value": Ref("a")})), outputs={"value": Ref("b")}).bind(CapabilityCatalog(moves, ["first", "second"]), catalog_version="1")
        result = await compile_script(bound, {}, asynchronous=True).arun()
        self.assertEqual(calls, ["sync", "async"])
        self.assertEqual(result.results["b"], "HELLO")

        async def cancel(value: str) -> str:
            raise asyncio.CancelledError

        script = compile_script(bind(cancel), {"value": "x"}, asynchronous=True)
        with self.assertRaises(asyncio.CancelledError):
            await script.arun()
        with self.assertRaises(RecoveryRequiredError):
            await script.arun()
