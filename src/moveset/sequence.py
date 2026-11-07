"""Optional compilation into func_seq's public Script and lifecycle contract."""

import sys

if sys.version_info < (3, 14):
    raise ImportError("The moveset sequence adapter requires Python 3.14 or newer")

import hashlib
import inspect

from func_seq import Ref as SequenceRef, Script

from .core import BoundPipeline, DefinitionError, Input, Ref, StepError, _Array, _Object, canonical, copy_json, resolve


def _mapping(value, inputs):
    if isinstance(value, Input):
        return resolve(value, inputs, {})
    if isinstance(value, Ref):
        return SequenceRef(value.step, value.path)
    if isinstance(value, _Object):
        return {key: _mapping(item, inputs) for key, item in value.items}
    if isinstance(value, _Array):
        return [_mapping(item, inputs) for item in value.items]
    return value


def _wrapper(bound, step, asynchronous):
    def invoke(arguments):
        try:
            result = bound.catalog.invoke(step.capability, arguments)
            if inspect.isawaitable(result):
                if inspect.iscoroutine(result):
                    result.close()
                raise DefinitionError("Capability returned an awaitable; compile with asynchronous=True")
            return copy_json(bound.catalog.serialize_result(step.capability, result))
        except Exception as error:
            raise StepError(step, str(error)) from error

    async def ainvoke(arguments):
        try:
            result = bound.catalog.invoke(step.capability, arguments)
            if inspect.isawaitable(result):
                result = await result
            return copy_json(bound.catalog.serialize_result(step.capability, result))
        except Exception as error:
            raise StepError(step, str(error)) from error

    return ainvoke if asynchronous else invoke


def compile_script(bound: BoundPipeline, inputs: dict, *, asynchronous: bool = False) -> Script:
    """Compile validated mappings; return func_seq's executor without wrapping state.

    Use asynchronous=True with Script.arun for mixed/awaitable capabilities. The
    execution mode is part of compatibility identity. Control, pace, observers,
    checkpoint, retry and restart are passed directly to Script.run/arun.
    """
    if type(asynchronous) is not bool:
        raise DefinitionError("asynchronous must be a boolean")
    inputs = bound.prepare(inputs)
    if not asynchronous and any(bound.catalog.describe(step.capability).is_async for step in bound.definition.steps):
        raise DefinitionError("Async capabilities require asynchronous=True and Script.arun")
    version = hashlib.sha256(canonical({"pipeline": bound.fingerprint, "inputs": inputs,
                                       "asynchronous": asynchronous})).hexdigest()
    script = Script(bound.definition.name, version=version)
    for step in bound.definition.steps:
        script.add_function(_wrapper(bound, step, asynchronous),
                            _mapping(step._arguments, inputs), step_id=step.id)
    return script
