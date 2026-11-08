"""Fallback JSON validation when hidden_moves does not export validate_json_value."""
from __future__ import annotations
import math
from collections.abc import Mapping
from typing import Any
from hidden_moves.adapters.errors import CapabilityExposureError

_KEYWORDS = frozenset({
	"$schema", "title", "description", "default", "examples", "type", "enum",
	"anyOf", "properties", "required", "additionalProperties", "items",
})
_TYPES = frozenset({"null", "boolean", "integer", "number", "string", "array", "object"})

class ValueValidationError(ValueError):
	"""A finite JSON value does not match the declared subset."""

def check_schema(schema: Mapping[str, Any]) -> None:
	if not isinstance(schema, Mapping):
		raise CapabilityExposureError("Schema nodes must be objects.")
	unknown = set(schema) - _KEYWORDS
	if unknown:
		raise CapabilityExposureError(f"Unsupported schema keywords: {", ".join(sorted(unknown))}.")
	if "type" in schema:
		types = schema["type"]
		if isinstance(types, str):
			types = (types,)
		if not isinstance(types, (list, tuple)) or not types or any(
			not isinstance(item, str) or item not in _TYPES for item in types
		):
			raise CapabilityExposureError("Schema type must be a supported JSON type or nonempty type list.")
	if "enum" in schema and (not isinstance(schema["enum"], (list, tuple)) or not schema["enum"]):
		raise CapabilityExposureError("Schema enum must be a nonempty array.")
	if "anyOf" in schema:
		branches = schema["anyOf"]
		if not isinstance(branches, (list, tuple)) or not branches:
			raise CapabilityExposureError("Schema anyOf must be a nonempty array.")
		for branch in branches:
			check_schema(branch)
	properties = schema.get("properties", {})
	if not isinstance(properties, Mapping):
		raise CapabilityExposureError("Schema properties must be an object.")
	for child in properties.values():
		check_schema(child)
	if "required" in schema:
		required = schema["required"]
		if not isinstance(required, (list, tuple)) or any(not isinstance(item, str) for item in required):
			raise CapabilityExposureError("Schema required must be an array of property names.")
		if len(required) != len(set(required)):
			raise CapabilityExposureError("Schema required names must be unique.")
	additional = schema.get("additionalProperties", True)
	if not isinstance(additional, bool):
		check_schema(additional)
	if additional is False and any(name not in properties for name in schema.get("required", ())):
		raise CapabilityExposureError("A closed schema requires a property it does not declare.")
	if "items" in schema:
		check_schema(schema["items"])

def _matches(value: Any, kind: str) -> bool:
	if kind == "null":
		return value is None
	if kind == "boolean":
		return isinstance(value, bool)
	if kind == "integer":
		return type(value) is int or (type(value) is float and math.isfinite(value) and value.is_integer())
	if kind == "number":
		return type(value) is int or (type(value) is float and math.isfinite(value))
	return isinstance(value, {"string": str, "array": list, "object": dict}[kind])

def _equal(first: Any, second: Any) -> bool:
	if isinstance(first, bool) != isinstance(second, bool):
		return False
	if isinstance(first, Mapping) and isinstance(second, Mapping):
		return first.keys() == second.keys() and all(_equal(first[key], second[key]) for key in first)
	if isinstance(first, (tuple, list)) and isinstance(second, (tuple, list)):
		return len(first) == len(second) and all(_equal(a, b) for a, b in zip(first, second))
	return first == second

def validate_value(schema: Mapping[str, Any], value: Any, path: str = "$arguments") -> None:
	if "type" in schema:
		types = schema["type"]
		types = (types,) if isinstance(types, str) else types
		if not any(_matches(value, kind) for kind in types):
			raise ValueValidationError(f"{path}: expected {" or ".join(types)}.")
	if "enum" in schema and not any(_equal(value, item) for item in schema["enum"]):
		raise ValueValidationError(f"{path}: value is outside the declared enum.")
	if "anyOf" in schema:
		for branch in schema["anyOf"]:
			try:
				validate_value(branch, value, path)
				break
			except ValueValidationError:
				pass
		else:
			raise ValueValidationError(f"{path}: value does not match any declared type.")
	if isinstance(value, dict):
		properties = schema.get("properties", {})
		for name in schema.get("required", ()):
			if name not in value:
				raise ValueValidationError(f"{path}.{name}: required argument is missing.")
		additional = schema.get("additionalProperties", True)
		for name, item in value.items():
			if name in properties:
				validate_value(properties[name], item, f"{path}.{name}")
			elif additional is False:
				raise ValueValidationError(f"{path}.{name}: unexpected argument.")
			elif isinstance(additional, Mapping):
				validate_value(additional, item, f"{path}.{name}")
	if isinstance(value, list) and "items" in schema:
		for index, item in enumerate(value):
			validate_value(schema["items"], item, f"{path}[{index}]")

def fallback_validate_json_value(schema: Mapping[str, Any], value: Any, path: str = "$value") -> Any:
	from hidden_moves import MoveSchemaError
	from hidden_moves.core.schema import json_value
	check_schema(schema)
	try:
		copied = json_value(value)
	except (MoveSchemaError, RecursionError) as error:
		raise ValueValidationError(f"{path}: {error}") from error
	validate_value(schema, copied, path)
	return copied
