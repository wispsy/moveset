# Optional durable execution

Install the `sequence` profile on Python 3.14+. The base import does not load
func_seq and remains usable on Python 3.11+.

```python
from moveset.sequence import compile_script
script = compile_script(bound, inputs)
progress = script.run(checkpoint="progress.json", pace="function")
progress = script.run(checkpoint="progress.json")
if progress.status == "completed":
    result = bound.finish(inputs, progress.results)
```

`compile_script(..., asynchronous=True)` supports `await script.arun()` for mixed
sync/async or sync-returned-awaitable operations. Execution mode is part of identity.
All mappings translate to func_seq values/references with the original step IDs.
The Script version hashes the bound fingerprint, run inputs and execution mode.
Mapping, schema, catalog version, output, input or mode changes reject old progress.
Callable/environment changes require the host to change catalog_version.

Use func_seq.Control, observers, pace, retry, restart and checkpoint directly.
Cooperative pause occurs at boundaries; cancellation propagates through arun.
Cancellation or failure may follow an effect: an uncertain action requires explicit
retry=True or restart=True. Completed steps do not replay on ordinary resume.
No new checkpoint format, executor, signal hook or exactly-once guarantee is added.
