# Moveset

Explicit linear composition of selected Python capabilities. Python 3.11+.

```python
from hidden_moves import Moves
from hidden_moves.adapters import CapabilityCatalog
from moveset import Input, Pipeline, Ref, Step

def normalize(text: str) -> str:
    return " ".join(text.lower().split())

moves = Moves()
moves.learn(normalize)
selected = CapabilityCatalog(moves, ["normalize"])
definition = Pipeline("example", version="1", steps=(
    Step("clean", "normalize", {"text": Input("text")}),
), outputs={"text": Ref("clean")})
bound = definition.bind(selected, catalog_version="text-v1")
assert bound.run({"text": " Hello WORLD "}).outputs == {"text": "hello world"}
```

Definitions snapshot nested arguments and outputs. `Input("payload")["key"][0]`
selects run data; `Ref("clean")` selects an earlier result. Mappings are explicit:
scalar results are never automatically wrapped in objects. Binding validates
selected capability names and known argument shapes. Run preflight validates all
known inputs before effects; dynamic result paths and values are checked before
the dependent call. Hidden Moves owns its documented schema subset and callable
binding. No provider is activated by creating or executing a pipeline.

`arun(inputs)` awaits mixed sync/async steps in order. `run` rejects declared async
steps before executing anything. Cancellation propagates; ordinary runs start
fresh. Results are caller-owned finite JSON snapshots, bounded to 1 MiB with depth
32; definitions have at most 256 steps. StepError identifies the step/capability
and retains the original cause. There is no retry, persistence or concurrency in
the core.

The bound fingerprint includes mappings, schemas, async flags and catalog version.
Change `catalog_version` when callable behavior, resources, defaults or configuration
change; this is not source authentication. The optional lifecycle adapter belongs
in the `sequence` profile and delegates durable execution to func_seq.

This source development profile pins Hidden Moves to an immutable revision. It is
independently built and versioned; release requirements and publication are tracked
separately in the [Wispsy Composition Project](https://github.com/orgs/wispsy/projects/23).

Build with `uv build --out-dir dist`; `python scripts/qualify.py dist` installs the
wheel and source archive outside the checkout and runs the contract tests. CI
qualifies Python 3.11 on Linux/Windows and Python 3.14 on Linux/macOS/Windows.
