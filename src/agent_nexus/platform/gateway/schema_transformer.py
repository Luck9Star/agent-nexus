"""JSON Schema -> Python type / Pydantic model converter.

Replaces the minimal ``_resolve_json_schema_type()`` in ``gateway.py`` with
full JSON Schema support including ``$ref`` resolution, ``allOf`` merging,
``oneOf``/``anyOf`` unions, and circular-reference handling.
"""

from __future__ import annotations

from typing import Any

from pydantic import BaseModel, Field, create_model

# ---------------------------------------------------------------------------
# Primitive type mapping
# ---------------------------------------------------------------------------

_PRIMITIVE_MAP: dict[str, type] = {
    "string": str,
    "integer": int,
    "number": float,
    "boolean": bool,
}

_STRING_FORMAT_MAP: dict[str, type] = {
    "date-time": str,  # keep as str — no datetime dependency
    "date": str,
    "time": str,
    "uri": str,
    "email": str,
    "uuid": str,
}


class SchemaTransformer:
    """JSON Schema -> Python type / Pydantic model converter.

    Binds a *full* schema document (one that may contain ``$defs`` or
    ``definitions``) so that ``$ref`` pointers can be resolved.

    Usage::

        transformer = SchemaTransformer(full_schema)
        py_type = transformer.resolve({"type": "string"})
        model   = transformer.resolve_model({"type": "object", "properties": {...}})
    """

    def __init__(self, full_schema: dict[str, Any]) -> None:
        self._full_schema = full_schema
        self._model_cache: dict[str, type[BaseModel]] = {}

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    def resolve(self, schema: dict[str, Any], name: str = "Anonymous") -> type:
        """Unified entry: auto-detect complexity and return a Python type.

        Simple schemas (primitives, plain arrays) return built-in types;
        complex schemas (objects, ``$ref``, ``allOf``) return dynamically
        created :class:`pydantic.BaseModel` subclasses.
        """
        return self._resolve_any(schema, name)

    def resolve_model(self, schema: dict[str, Any], name: str = "DynamicModel") -> type[BaseModel]:
        """Explicit: force-generate a Pydantic BaseModel regardless of schema complexity."""
        result = self._resolve_any(schema, name)
        if isinstance(result, type) and issubclass(result, BaseModel):
            return result
        # Wrap a primitive / plain type into a single-field model
        return create_model(name, value=(result, ...))  # type: ignore[call-overload]

    # ------------------------------------------------------------------
    # Internal dispatch
    # ------------------------------------------------------------------

    def _resolve_any(self, schema: dict[str, Any], name: str = "Anonymous") -> type:  # noqa: PLR0911, PLR0912
        """Dispatch based on schema keywords."""
        # 1. ``$ref`` — highest priority
        if "$ref" in schema:
            resolved = self._resolve_ref(schema["$ref"], name)
            nullable = schema.get("nullable", False)
            return resolved | None if nullable else resolved  # type: ignore[return-value]

        # 2. ``allOf`` — merge all sub-schemas into one model
        if "allOf" in schema:
            return self._resolve_all_of(schema["allOf"], name)

        # 3. ``oneOf`` / ``anyOf``
        for key in ("oneOf", "anyOf"):
            if key in schema:
                return self._resolve_one_of_any_of(schema[key], name)

        # 4. Explicit ``type``
        type_str = schema.get("type")

        if isinstance(type_str, list):
            # OpenAPI 3.1 style: ["string", "null"]
            non_null = [t for t in type_str if t != "null"]
            has_null = "null" in type_str
            inner_type = self._resolve_typed(non_null[0], schema, name) if non_null else str
            return inner_type | None if has_null else inner_type  # type: ignore[return-value]

        if isinstance(type_str, str):
            nullable = schema.get("nullable", False)
            inner = self._resolve_typed(type_str, schema, name)
            return inner | None if nullable else inner  # type: ignore[return-value]

        # 5. Fallback — treat as object if properties present, else str
        if "properties" in schema:
            return self._build_object_model(schema, name)

        return str

    # ------------------------------------------------------------------
    # Type-specific resolvers
    # ------------------------------------------------------------------

    def _resolve_typed(self, type_str: str, schema: dict[str, Any], name: str) -> type:
        """Resolve a schema with an explicit ``type`` field."""
        if type_str in _PRIMITIVE_MAP:
            fmt = schema.get("format")
            if type_str == "string" and fmt in _STRING_FORMAT_MAP:
                return _STRING_FORMAT_MAP[fmt]
            return _PRIMITIVE_MAP[type_str]

        if type_str == "array":
            items = schema.get("items", {})
            item_type = self._resolve_any(items, f"{name}Item") if isinstance(items, dict) else Any  # type: ignore[assignment]
            return list[item_type]  # type: ignore[valid-type]

        if type_str == "object":
            return self._build_object_model(schema, name)

        return str

    def _resolve_ref(self, ref: str, _name: str = "RefModel") -> type:
        """Resolve a ``$ref`` pointer (``#/$defs/X`` or ``#/definitions/X``)."""
        if not ref.startswith("#/"):
            # External refs are not supported — degrade to str
            return str

        parts = ref[2:].split("/")
        ref_name = parts[-1]

        # Check cache first (breaks circular refs)
        if ref_name in self._model_cache:
            return self._model_cache[ref_name]

        # Navigate the full schema to the referenced definition
        resolved_schema: Any = self._full_schema
        for part in parts:
            if isinstance(resolved_schema, dict):
                resolved_schema = resolved_schema.get(part, {})
            else:
                resolved_schema = {}
                break

        if not isinstance(resolved_schema, dict) or not resolved_schema:
            return str

        # Insert a placeholder *before* recursing to handle circular refs
        placeholder: type[BaseModel] = create_model(ref_name)  # type: ignore[call-overload]
        self._model_cache[ref_name] = placeholder

        actual = self._resolve_any(resolved_schema, ref_name)

        # If recursion produced the same placeholder, keep it
        if actual is not placeholder and isinstance(actual, type) and issubclass(actual, BaseModel):
            self._model_cache[ref_name] = actual

        return self._model_cache[ref_name]

    def _resolve_all_of(self, sub_schemas: list[dict[str, Any]], name: str) -> type[BaseModel]:
        """Merge all ``allOf`` sub-schemas into a single BaseModel."""
        merged_props: dict[str, Any] = {}

        for sub in sub_schemas:
            if "$ref" in sub:
                ref_type = self._resolve_ref(sub["$ref"], name)
                if isinstance(ref_type, type) and issubclass(ref_type, BaseModel):
                    for field_name, field_info in ref_type.model_fields.items():
                        merged_props[field_name] = (
                            field_info.annotation,
                            field_info.default,
                        )
            elif "properties" in sub:
                self._merge_properties(sub, merged_props)

        return create_model(name, **merged_props)  # type: ignore[call-overload]

    def _resolve_one_of_any_of(self, variants: list[dict[str, Any]], name: str) -> type:
        """Resolve ``oneOf`` / ``anyOf`` into ``Union[...]`` or ``X | None``."""
        if not variants:
            return str

        resolved_variants: list[type] = []
        has_null = False

        for variant in variants:
            if not isinstance(variant, dict):
                continue
            vtype = variant.get("type")
            if vtype == "null":
                has_null = True
                continue
            resolved_variants.append(self._resolve_any(variant, name))

        if not resolved_variants:
            return type(None)  # everything was null

        # Single non-null variant + null → Optional
        if len(resolved_variants) == 1:
            return resolved_variants[0] | None if has_null else resolved_variants[0]  # type: ignore[return-value]

        # Multiple non-null variants — build Union[X, Y, ...]
        union = resolved_variants[0]
        for v in resolved_variants[1:]:
            union = union | v  # type: ignore[assignment]
        return union | None if has_null else union  # type: ignore[return-value]

    # ------------------------------------------------------------------
    # Object model builder
    # ------------------------------------------------------------------

    def _build_object_model(self, schema: dict[str, Any], name: str) -> type[BaseModel]:
        """Build a dynamic Pydantic model from an object schema."""
        if name in self._model_cache:
            return self._model_cache[name]

        properties = schema.get("properties", {})
        required = set(schema.get("required", []))

        # Insert placeholder to break cycles
        placeholder: type[BaseModel] = create_model(name)  # type: ignore[call-overload]
        self._model_cache[name] = placeholder

        fields: dict[str, Any] = {}
        for prop_name, prop_def in properties.items():
            if not isinstance(prop_def, dict):
                fields[prop_name] = (Any, ...)
                continue

            prop_type = self._resolve_any(prop_def, _capitalize(prop_name))

            if prop_name in required:
                fields[prop_name] = (prop_type, ...)
            else:
                default = prop_def.get("default", ...)
                if default is not ...:
                    fields[prop_name] = (prop_type, Field(default=default))
                else:
                    fields[prop_name] = (prop_type, None)

        model = create_model(name, **fields)  # type: ignore[call-overload]
        self._model_cache[name] = model
        return model

    # ------------------------------------------------------------------
    # Helpers
    # ------------------------------------------------------------------

    def _merge_properties(
        self,
        schema: dict[str, Any],
        target: dict[str, Any],
    ) -> None:
        """Merge properties from *schema* into *target* dict."""
        properties = schema.get("properties", {})
        required = set(schema.get("required", []))

        for prop_name, prop_def in properties.items():
            if not isinstance(prop_def, dict):
                target[prop_name] = (Any, ...)
                continue
            prop_type = self._resolve_any(prop_def, _capitalize(prop_name))
            if prop_name in required:
                target[prop_name] = (prop_type, ...)
            else:
                default = prop_def.get("default", ...)
                target[prop_name] = (prop_type, default)


def _capitalize(name: str) -> str:
    """Upper-case the first letter of *name* for model naming."""
    return name[0].upper() + name[1:] if name else "Anonymous"
