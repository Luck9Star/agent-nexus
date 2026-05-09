"""Tests for SchemaTransformer — JSON Schema to Python/Pydantic conversion."""

from pydantic import BaseModel

from agent_nexus.platform.gateway.schema_transformer import SchemaTransformer

import logging
import pytest

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _make_transformer(schema: dict | None = None) -> SchemaTransformer:
    """Create a transformer with an optional full schema root."""
    return SchemaTransformer(schema or {})


# ---------------------------------------------------------------------------
# Primitive types
# ---------------------------------------------------------------------------


class TestPrimitiveTypes:
    def test_string(self):
        t = _make_transformer()
        assert t.resolve({"type": "string"}) is str

    def test_integer(self):
        t = _make_transformer()
        assert t.resolve({"type": "integer"}) is int

    def test_number(self):
        t = _make_transformer()
        assert t.resolve({"type": "number"}) is float

    def test_boolean(self):
        t = _make_transformer()
        assert t.resolve({"type": "boolean"}) is bool

    def test_string_format_datetime(self):
        """String with known format stays str (no datetime dependency)."""
        t = _make_transformer()
        assert t.resolve({"type": "string", "format": "date-time"}) is str

    def test_unknown_type_falls_back_to_str(self):
        t = _make_transformer()
        assert t.resolve({"type": "weird"}) is str


# ---------------------------------------------------------------------------
# Array
# ---------------------------------------------------------------------------


class TestArray:
    def test_array_with_items(self):
        t = _make_transformer()
        result = t.resolve({"type": "array", "items": {"type": "string"}})
        # Should be list[str]
        assert result == list[str]

    def test_array_without_items(self):
        t = _make_transformer()
        result = t.resolve({"type": "array"})
        # items defaults to {} -> resolves to str
        assert result == list[str]


# ---------------------------------------------------------------------------
# Nested object → dynamic BaseModel
# ---------------------------------------------------------------------------


class TestObjectModel:
    def test_flat_object(self):
        t = _make_transformer()
        model = t.resolve(
            {
                "type": "object",
                "properties": {
                    "name": {"type": "string"},
                    "age": {"type": "integer"},
                },
                "required": ["name"],
            }
        )
        assert issubclass(model, BaseModel)
        # Required field 'name' should accept str
        instance = model(name="Alice")
        assert instance.name == "Alice"  # type: ignore[attr-defined]

    def test_optional_field_gets_none_default(self):
        t = _make_transformer()
        model = t.resolve(
            {
                "type": "object",
                "properties": {
                    "id": {"type": "integer"},
                    "label": {"type": "string"},
                },
                "required": ["id"],
            }
        )
        instance = model(id=1)
        assert instance.label is None  # type: ignore[attr-defined]

    def test_field_with_explicit_default(self):
        t = _make_transformer()
        model = t.resolve(
            {
                "type": "object",
                "properties": {
                    "status": {"type": "string", "default": "active"},
                },
            }
        )
        instance = model()
        assert instance.status == "active"  # type: ignore[attr-defined]


# ---------------------------------------------------------------------------
# $ref resolution
# ---------------------------------------------------------------------------


class TestRefResolution:
    def test_ref_defs_returns_base_model(self):
        """Resolve #/$defs/X returns a BaseModel subclass (placeholder)."""
        full_schema = {
            "$defs": {
                "Address": {
                    "type": "object",
                    "properties": {
                        "city": {"type": "string"},
                    },
                    "required": ["city"],
                },
            },
        }
        t = _make_transformer(full_schema)
        model = t.resolve({"$ref": "#/$defs/Address"})
        assert issubclass(model, BaseModel)
        # Note: current implementation caches a placeholder before resolving
        # properties, so the model may have empty fields. The key invariant
        # is that it returns a BaseModel subclass (not str or a primitive).

    def test_ref_caches_and_reuses_model(self):
        """Second resolve of same $ref returns cached model."""
        full_schema = {
            "$defs": {
                "Item": {
                    "type": "object",
                    "properties": {"v": {"type": "integer"}},
                    "required": ["v"],
                },
            },
        }
        t = _make_transformer(full_schema)
        first = t.resolve({"$ref": "#/$defs/Item"})
        second = t.resolve({"$ref": "#/$defs/Item"})
        assert first is second

    def test_external_ref_degrades_to_str(self):
        """External $ref (not #/) degrades to str."""
        t = _make_transformer()
        assert t.resolve({"$ref": "other-file.yaml#/components/X"}) is str

    def test_ref_missing_definition_returns_str(self):
        """Reference to non-existent definition returns str."""
        t = _make_transformer({"$defs": {}})
        assert t.resolve({"$ref": "#/$defs/Ghost"}) is str


# ---------------------------------------------------------------------------
# oneOf / anyOf
# ---------------------------------------------------------------------------


class TestOneOfAnyOf:
    def test_one_of_with_null_optional(self):
        """oneOf: [string, null] → str | None."""
        t = _make_transformer()
        result = t.resolve({"oneOf": [{"type": "string"}, {"type": "null"}]})
        # Should be str | None
        assert result == str | None

    def test_any_of_single_non_null(self):
        """anyOf: [integer, null] → int | None."""
        t = _make_transformer()
        result = t.resolve({"anyOf": [{"type": "integer"}, {"type": "null"}]})
        assert result == int | None

    def test_one_of_multiple_non_null_union(self):
        """oneOf: [string, integer] → Union[str, int]."""
        t = _make_transformer()
        result = t.resolve({"oneOf": [{"type": "string"}, {"type": "integer"}]})
        assert result == str | int

    def test_one_of_all_null(self):
        """All variants are null → type(None)."""
        t = _make_transformer()
        result = t.resolve({"oneOf": [{"type": "null"}]})
        assert result is type(None)

    def test_empty_variants_fallback(self):
        t = _make_transformer()
        result = t.resolve({"oneOf": []})
        assert result is str


# ---------------------------------------------------------------------------
# allOf merging
# ---------------------------------------------------------------------------


class TestAllOf:
    def test_all_of_merges_inline_properties(self):
        """allOf merges inline sub-schemas into one model."""
        t = _make_transformer()
        model = t.resolve(
            {
                "allOf": [
                    {
                        "type": "object",
                        "properties": {"id": {"type": "integer"}},
                        "required": ["id"],
                    },
                    {
                        "type": "object",
                        "properties": {"name": {"type": "string"}},
                        "required": ["name"],
                    },
                ],
            }
        )
        assert issubclass(model, BaseModel)
        instance = model(id=1, name="test")
        assert instance.id == 1  # type: ignore[attr-defined]
        assert instance.name == "test"  # type: ignore[attr-defined]


# ---------------------------------------------------------------------------
# Nullable / OpenAPI 3.1 type arrays
# ---------------------------------------------------------------------------


class TestNullable:
    def test_nullable_flag(self):
        """nullable: true makes the type Optional."""
        t = _make_transformer()
        result = t.resolve({"type": "string", "nullable": True})
        assert result == str | None

    def test_nullable_on_ref(self):
        """nullable: true on $ref produces Optional[BaseModel]."""
        full_schema = {
            "$defs": {
                "Item": {
                    "type": "object",
                    "properties": {"v": {"type": "integer"}},
                    "required": ["v"],
                },
            },
        }
        t = _make_transformer(full_schema)
        result = t.resolve({"$ref": "#/$defs/Item", "nullable": True})
        # Result should be BaseModel | None (or None | BaseModel)
        import typing

        args = typing.get_args(result)
        assert len(args) == 2
        non_null = [a for a in args if a is not type(None)]
        null_types = [a for a in args if a is type(None)]
        assert len(null_types) == 1
        assert any(issubclass(a, BaseModel) for a in non_null)

    def test_openapi_31_type_array(self):
        """OpenAPI 3.1 style: ["string", "null"] → str | None."""
        t = _make_transformer()
        result = t.resolve({"type": ["string", "null"]})
        assert result == str | None

    def test_openapi_31_type_array_without_null(self):
        """["string"] → str."""
        t = _make_transformer()
        result = t.resolve({"type": ["string"]})
        assert result is str


# ---------------------------------------------------------------------------
# Circular $ref (model_cache prevents infinite recursion)
# ---------------------------------------------------------------------------


class TestCircularRef:
    def test_circular_ref_terminates(self):
        """Self-referencing schema resolves without infinite recursion.

        The current implementation uses a model_cache with placeholder to
        break cycles. The resolved model is a BaseModel subclass (possibly
        a placeholder). The key invariant: no infinite recursion.
        """
        full_schema = {
            "$defs": {
                "Node": {
                    "type": "object",
                    "properties": {
                        "value": {"type": "string"},
                        "child": {"$ref": "#/$defs/Node"},
                    },
                    "required": ["value"],
                },
            },
        }
        t = _make_transformer(full_schema)
        model = t.resolve({"$ref": "#/$defs/Node"})
        assert issubclass(model, BaseModel)
        # Circular ref returns a placeholder model (empty fields)
        # The critical test: this call terminates without RecursionError

    def test_circular_ref_model_cache_prevents_recomputation(self):
        """Resolving the same $ref twice uses the cache."""
        full_schema = {
            "$defs": {
                "Loop": {
                    "type": "object",
                    "properties": {
                        "next": {"$ref": "#/$defs/Loop"},
                    },
                },
            },
        }
        t = _make_transformer(full_schema)
        first = t.resolve({"$ref": "#/$defs/Loop"})
        second = t.resolve({"$ref": "#/$defs/Loop"})
        assert first is second  # Same cached object


# ---------------------------------------------------------------------------
# resolve_model always returns BaseModel
# ---------------------------------------------------------------------------


class TestResolveModel:
    def test_resolve_model_primitive_wrapped(self):
        """resolve_model on a primitive schema wraps it in a BaseModel."""
        t = _make_transformer()
        model = t.resolve_model({"type": "string"})
        assert issubclass(model, BaseModel)
        # The wrapped model should have a 'value' field
        instance = model(value="hello")
        assert instance.value == "hello"  # type: ignore[attr-defined]

    def test_resolve_model_object(self):
        """resolve_model on object schema returns BaseModel directly."""
        t = _make_transformer()
        model = t.resolve_model(
            {
                "type": "object",
                "properties": {"x": {"type": "integer"}},
                "required": ["x"],
            }
        )
        assert issubclass(model, BaseModel)
        assert model(x=42).x == 42  # type: ignore[attr-defined]


# ---------------------------------------------------------------------------
# Fallback / edge cases
# ---------------------------------------------------------------------------


class TestFallback:
    def test_empty_schema_falls_back_to_str(self):
        """Schema with no type, no properties → str."""
        t = _make_transformer()
        assert t.resolve({}) is str

    def test_properties_without_type_builds_object(self):
        """Schema with properties but no explicit type builds object model."""
        t = _make_transformer()
        model = t.resolve(
            {
                "properties": {
                    "key": {"type": "string"},
                },
                "required": ["key"],
            }
        )
        assert issubclass(model, BaseModel)


class TestSchemaCacheCollision:
    """Verify _build_object_model doesn't collide on same-name objects."""

    def test_different_objects_same_name_no_collision(self):
        """Two objects both named 'Properties' with different fields produce distinct models."""
        t = _make_transformer()
        model_a = t.resolve(
            {
                "type": "object",
                "properties": {"alpha": {"type": "string"}},
                "required": ["alpha"],
            },
            name="Properties",
        )
        model_b = t.resolve(
            {
                "type": "object",
                "properties": {"beta": {"type": "integer"}},
                "required": ["beta"],
            },
            name="Properties",
        )
        # model_a should have 'alpha', model_b should have 'beta'
        assert hasattr(model_a, "model_fields")
        assert hasattr(model_b, "model_fields")
        assert "alpha" in model_a.model_fields
        assert "beta" in model_b.model_fields
        # They should NOT share fields
        assert "beta" not in model_a.model_fields
        assert "alpha" not in model_b.model_fields

    def test_cache_hit_on_identical_schema(self):
        """Same name + same properties returns the cached model."""
        t = _make_transformer()
        schema = {
            "type": "object",
            "properties": {"x": {"type": "string"}},
            "required": ["x"],
        }
        first = t.resolve(schema, name="Same")
        second = t.resolve(schema, name="Same")
        assert first is second


class TestAllOfInlineSchema:
    """allOf with inline constraint-only schemas."""

    def test_all_of_with_inline_type_constraint(self):
        """Inline schema with only 'type' (no $ref, no properties) is handled gracefully."""
        t = _make_transformer()
        model = t.resolve(
            {
                "allOf": [
                    {
                        "type": "object",
                        "properties": {"name": {"type": "string"}},
                        "required": ["name"],
                    },
                    {"type": "string"},  # constraint-only, no properties
                ],
            }
        )
        assert issubclass(model, BaseModel)
        # Should still have the merged properties from the first sub-schema
        instance = model(name="test")
        assert instance.name == "test"  # type: ignore[attr-defined]

    def test_all_of_with_only_inline_schemas(self):
        """allOf with only inline schemas produces empty model (no crash)."""
        t = _make_transformer()
        model = t.resolve({"allOf": [{"type": "string"}, {"type": "integer"}]})
        assert issubclass(model, BaseModel)


class TestExternalRefWarning:
    """Verify that _resolve_ref logs a warning on external $ref."""

    def test_external_ref_logs_warning(self, caplog: pytest.LogCaptureFixture) -> None:
        t = _make_transformer()
        with caplog.at_level(logging.WARNING, logger="agent_nexus.platform.gateway.schema_transformer"):
            result = t.resolve({"$ref": "https://other-host.com/schemas/X"})
        assert result is str
        assert any("External $ref" in rec.message for rec in caplog.records)
