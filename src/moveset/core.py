"""Explicit data mappings; capability and lifecycle behavior belongs to their owners."""

from __future__ import annotations

from dataclasses import dataclass
import hashlib
import inspect
import json
import math
import re
from typing import Any

from hidden_moves.adapters import CapabilityCatalog, validate_json_value

MAX_BYTES = 1_048_576
MAX_DEPTH = 32
MAX_STEPS = 256


class PipelineError(ValueError):
    """A definition or value cannot be used by this pipeline."""


class DefinitionError(PipelineError):
    """A passive definition is invalid."""


class StepError(PipelineError):
    """A selected operation or one of its mappings failed."""

    def __init__(self, step: Step, message: str):
        self.step_id = step.id
        self.capability = step.capability
        super().__init__(f"step {step.id!r} ({step.capability}): {message}")


def identifier(value: str) -> str:
    if type(value) is not str or not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9_.-]{0,127}", value):
        raise DefinitionError("Identifiers require 1–128 ASCII letters, digits, dots, underscores or hyphens")
    return value


def canonical(value: Any) -> bytes:
    try:
        data = json.dumps(value, ensure_ascii=False, allow_nan=False, sort_keys=True,
                          separators=(",", ":")).encode("utf-8")
    except (TypeError, ValueError, UnicodeError, RecursionError) as error:
        raise PipelineError("Expected finite UTF-8 JSON") from error
    if len(data) > MAX_BYTES:
        raise PipelineError(f"JSON exceeds {MAX_BYTES} bytes")
    return data


@dataclass(frozen=True)
class Input:
    name: str
    path: tuple[str | int, ...] = ()

    def __post_init__(self):
        identifier(self.name)
        _path(self.path)

    def __getitem__(self, part: str | int) -> Input:
        return Input(self.name, (*self.path, part))


@dataclass(frozen=True)
class Ref:
    step: str
    path: tuple[str | int, ...] = ()

    def __post_init__(self):
        identifier(self.step)
        _path(self.path)

    def __getitem__(self, part: str | int) -> Ref:
        return Ref(self.step, (*self.path, part))


def _path(path):
    if type(path) is not tuple or len(path) > MAX_DEPTH or any(
        type(part) not in (str, int) or (type(part) is int and part < 0) for part in path
    ):
        raise DefinitionError("Paths must be bounded tuples of string keys or nonnegative indexes")
    canonical(list(path))


@dataclass(frozen=True)
class _Object:
    items: tuple


@dataclass(frozen=True)
class _Array:
    items: tuple


def freeze(value, depth=0, *, expressions=True):
    if depth > MAX_DEPTH:
        raise DefinitionError(f"Values exceed depth {MAX_DEPTH}")
    if expressions and isinstance(value, (Input, Ref)):
        return value
    if type(value) is dict:
        if any(type(key) is not str for key in value):
            raise DefinitionError("Object keys must be strings")
        return _Object(tuple((key, freeze(item, depth + 1, expressions=expressions)) for key, item in value.items()))
    if type(value) is list:
        return _Array(tuple(freeze(item, depth + 1, expressions=expressions) for item in value))
    if value is None or type(value) in (str, bool, int) or (type(value) is float and math.isfinite(value)):
        canonical(value)
        return value
    raise DefinitionError("Values must be finite JSON or explicit Input/Ref expressions")


def thaw(value):
    if isinstance(value, _Object):
        return {key: thaw(item) for key, item in value.items}
    if isinstance(value, _Array):
        return [thaw(item) for item in value.items]
    return value


def copy_json(value):
    copied = thaw(freeze(value, expressions=False))
    canonical(copied)
    return copied


def expression(value):
    if isinstance(value, Input):
        return {"input": {"name": value.name, "path": list(value.path)}}
    if isinstance(value, Ref):
        return {"ref": {"step": value.step, "path": list(value.path)}}
    if isinstance(value, _Object):
        return {"object": {key: expression(item) for key, item in value.items}}
    if isinstance(value, _Array):
        return {"array": [expression(item) for item in value.items]}
    return {"value": value}


def references(value):
    if isinstance(value, (Ref, Input)):
        yield value
    elif isinstance(value, (_Object, _Array)):
        for item in (item for _, item in value.items) if isinstance(value, _Object) else value.items:
            yield from references(item)


def resolve(value, inputs, results):
    if isinstance(value, (Input, Ref)):
        selected = inputs if isinstance(value, Input) else results
        key = value.name if isinstance(value, Input) else value.step
        try:
            selected = selected[key]
            for part in value.path:
                if (type(part) is str and type(selected) is dict) or (type(part) is int and type(selected) is list):
                    selected = selected[part]
                else:
                    raise KeyError(part)
        except (KeyError, IndexError) as error:
            raise PipelineError(f"Cannot resolve {expression(value)}") from error
        return copy_json(selected)
    if isinstance(value, _Object):
        return {key: resolve(item, inputs, results) for key, item in value.items}
    if isinstance(value, _Array):
        return [resolve(item, inputs, results) for item in value.items]
    return value


@dataclass(frozen=True, init=False)
class Step:
    id: str
    capability: str
    _arguments: _Object

    def __init__(self, id: str, capability: str, arguments: dict):
        identifier(id)
        if type(capability) is not str or not capability or len(capability) > 512:
            raise DefinitionError("A step requires a bounded capability name")
        if type(arguments) is not dict:
            raise DefinitionError("Step arguments must be an object")
        object.__setattr__(self, "id", id)
        object.__setattr__(self, "capability", capability)
        object.__setattr__(self, "_arguments", freeze(arguments))
        canonical(expression(self._arguments))

    @property
    def arguments(self):
        return thaw(self._arguments)


@dataclass(frozen=True, init=False)
class Pipeline:
    name: str
    version: str
    steps: tuple[Step, ...]
    _outputs: _Object

    def __init__(self, name: str, *, version: str, steps: tuple[Step, ...], outputs: dict):
        identifier(name)
        identifier(version)
        if type(steps) is not tuple or not 1 <= len(steps) <= MAX_STEPS or any(type(step) is not Step for step in steps):
            raise DefinitionError(f"A pipeline requires a tuple of 1–{MAX_STEPS} Steps")
        previous = set()
        for step in steps:
            if step.id in previous:
                raise DefinitionError(f"Duplicate step {step.id!r}")
            for ref in references(step._arguments):
                if isinstance(ref, Ref) and ref.step not in previous:
                    raise DefinitionError(f"step {step.id!r}: reference must name an earlier step: {ref.step!r}")
            previous.add(step.id)
        if type(outputs) is not dict:
            raise DefinitionError("Outputs must be an explicit object")
        frozen = freeze(outputs)
        if any(isinstance(ref, Ref) and ref.step not in previous for ref in references(frozen)):
            raise DefinitionError("Output reference names an unknown step")
        for key, value in (("name", name), ("version", version), ("steps", steps), ("_outputs", frozen)):
            object.__setattr__(self, key, value)
        canonical(self.manifest)

    @property
    def outputs(self):
        return thaw(self._outputs)

    @property
    def manifest(self):
        return {"schema": 1, "name": self.name, "version": self.version,
                "steps": [{"id": step.id, "capability": step.capability,
                           "arguments": expression(step._arguments)} for step in self.steps],
                "outputs": expression(self._outputs)}

    def bind(self, catalog: CapabilityCatalog, *, catalog_version: str) -> BoundPipeline:
        return BoundPipeline(self, catalog, catalog_version)


def _partial(schema, value, inputs=None):
    """Validate known portions; unknown producer values are checked at invocation."""
    if isinstance(value, Ref):
        return
    if isinstance(value, Input):
        if inputs is not None:
            validate_json_value(schema, resolve(value, inputs, {}))
        return
    if "anyOf" in schema:
        for branch in schema["anyOf"]:
            try:
                _partial(branch, value, inputs)
                break
            except (ValueError, TypeError):
                pass
        else:
            raise DefinitionError("Mapping does not match any declared type")
    if isinstance(value, _Object):
        values = dict(value.items)
        if "type" in schema:
            validate_json_value({"type": schema["type"]}, {})
        if any(name not in values for name in schema.get("required", ())):
            raise DefinitionError("A required argument is missing")
        properties = schema.get("properties", {})
        additional = schema.get("additionalProperties", True)
        for key, item in value.items:
            if key not in properties and additional is False:
                raise DefinitionError(f"Unexpected argument {key!r}")
            _partial(properties.get(key, additional if isinstance(additional, dict) else {}), item, inputs)
    elif isinstance(value, _Array):
        if "type" in schema:
            validate_json_value({"type": schema["type"]}, [])
        for item in value.items:
            _partial(schema.get("items", {}), item, inputs)
    else:
        validate_json_value(schema, value)
    if not tuple(references(value)):
        validate_json_value(schema, thaw(value))


@dataclass(frozen=True)
class Result:
    """A run's caller-owned JSON snapshots."""

    results: dict
    outputs: dict


@dataclass(frozen=True, init=False)
class BoundPipeline:
    definition: Pipeline
    catalog: CapabilityCatalog
    catalog_version: str

    def __init__(self, definition, catalog, catalog_version):
        if not isinstance(catalog, CapabilityCatalog):
            raise DefinitionError("Bind an explicitly selected CapabilityCatalog")
        identifier(catalog_version)
        for step in definition.steps:
            try:
                schema = _schema_json(catalog.describe(step.capability).input_schema)
                _partial(schema, step._arguments)
            except Exception as error:
                raise StepError(step, str(error)) from error
        object.__setattr__(self, "definition", definition)
        object.__setattr__(self, "catalog", catalog)
        object.__setattr__(self, "catalog_version", catalog_version)
        canonical(self.manifest)

    @property
    def manifest(self):
        names = sorted({step.capability for step in self.definition.steps})
        return {"definition": self.definition.manifest, "catalog_version": self.catalog_version,
                "capabilities": [{"name": name, "input": _schema_json(self.catalog.describe(name).input_schema),
                                  "output": _schema_json(self.catalog.describe(name).output_schema),
                                  "async": self.catalog.describe(name).is_async} for name in names]}

    @property
    def fingerprint(self):
        return hashlib.sha256(canonical(self.manifest)).hexdigest()

    def prepare(self, inputs: dict):
        if type(inputs) is not dict:
            raise PipelineError("Run inputs must be a JSON object")
        inputs = copy_json(inputs)
        for step in self.definition.steps:
            try:
                _partial(_schema_json(self.catalog.describe(step.capability).input_schema), step._arguments, inputs)
            except Exception as error:
                raise StepError(step, str(error)) from error
        for ref in references(self.definition._outputs):
            if isinstance(ref, Input):
                resolve(ref, inputs, {})
        return inputs

    def finish(self, inputs, results):
        return Result(copy_json(results), resolve(self.definition._outputs, inputs, results))

    def _invoke(self, step, inputs, results):
        return self.catalog.invoke(step.capability, resolve(step._arguments, inputs, results))

    def run(self, inputs: dict) -> Result:
        inputs = self.prepare(inputs)
        if any(self.catalog.describe(step.capability).is_async for step in self.definition.steps):
            raise DefinitionError("Pipeline contains async capabilities; use arun()")
        results = {}
        for step in self.definition.steps:
            try:
                result = self._invoke(step, inputs, results)
                if inspect.isawaitable(result):
                    if inspect.iscoroutine(result):
                        result.close()
                    raise DefinitionError("Capability returned an awaitable; use arun()")
                results[step.id] = copy_json(self.catalog.serialize_result(step.capability, result))
            except Exception as error:
                raise StepError(step, str(error)) from error
        return self.finish(inputs, results)

    async def arun(self, inputs: dict) -> Result:
        inputs = self.prepare(inputs)
        results = {}
        for step in self.definition.steps:
            try:
                result = self._invoke(step, inputs, results)
                if inspect.isawaitable(result):
                    result = await result
                results[step.id] = copy_json(self.catalog.serialize_result(step.capability, result))
            except Exception as error:
                raise StepError(step, str(error)) from error
        return self.finish(inputs, results)


def _schema_json(schema):
    """Catalog metadata uses frozen mappings/tuples; normalize without changing values."""
    from collections.abc import Mapping

    if isinstance(schema, Mapping):
        return {key: _schema_json(item) for key, item in schema.items()}
    if isinstance(schema, (list, tuple)):
        return [_schema_json(item) for item in schema]
    return schema
