"""Synthetic effects for actual independent-process checkpoint tests."""

import json
from pathlib import Path
import sys

from hidden_moves import Moves
from hidden_moves.adapters import CapabilityCatalog
from moveset import Input, Pipeline, Ref, Step
from moveset.sequence import compile_script


def effect(value: str, receipt: str) -> str:
    with Path(receipt).open("a", encoding="utf-8") as output:
        output.write(value + "\n")
    return value


def main():
    checkpoint, receipt, mode, catalog_version = sys.argv[1:]
    moves = Moves()
    moves.learn(effect)
    bound = Pipeline("processes", version="1", steps=(
        Step("first", "effect", {"value": Input("text"), "receipt": receipt}),
        Step("second", "effect", {"value": Ref("first"), "receipt": receipt}),
    ), outputs={"text": Ref("second")}).bind(CapabilityCatalog(moves, ["effect"]), catalog_version=catalog_version)
    script = compile_script(bound, {"text": "changed" if mode == "input" else "hello"})
    result = script.run(checkpoint=checkpoint, pace="function" if mode == "pause" else "auto")
    print(json.dumps({"status": result.status, "completed": result.completed}))


if __name__ == "__main__":
    main()
