"""Schema inference tool -- convert type annotations to JSON Schema.

Supports Python type annotations (dataclass, Pydantic style) and
TypeScript interfaces. Maps types to JSON Schema equivalents.
"""

from __future__ import annotations

import re

from agent_api_doc_generator.models import SchemaInfo

# Type mapping: source type -> JSON Schema type
PYTHON_TYPE_MAP: dict[str, dict[str, str]] = {
    "str": {"type": "string"},
    "int": {"type": "integer"},
    "float": {"type": "number"},
    "bool": {"type": "boolean"},
    "dict": {"type": "object"},
    "list": {"type": "array"},
    "bytes": {"type": "string", "format": "binary"},
    "datetime": {"type": "string", "format": "date-time"},
    "date": {"type": "string", "format": "date"},
    "uuid": {"type": "string", "format": "uuid"},
    "any": {"type": "object"},
}

TYPESCRIPT_TYPE_MAP: dict[str, dict[str, str]] = {
    "string": {"type": "string"},
    "number": {"type": "number"},
    "boolean": {"type": "boolean"},
    "object": {"type": "object"},
    "array": {"type": "array"},
    "any": {"type": "object"},
    "Date": {"type": "string", "format": "date-time"},
}

# Regex patterns for class/interface field extraction
PYTHON_FIELD_RE = re.compile(
    r"^\s+(\w+)\s*:\s*(Optional\[)?(\w+)(\])?",
    re.MULTILINE,
)

TYPESCRIPT_FIELD_RE = re.compile(
    r"^\s+(\w+)(\??):\s*(\w+)(?:\[\])?",
    re.MULTILINE,
)


def _is_python_type_info(type_info: str) -> bool:
    """Check if the type info looks like Python annotations."""
    return bool(re.search(r"(?:class\s+\w+:|:\s*(?:str|int|float|bool|Optional|list|dict))", type_info))


def _is_typescript_type_info(type_info: str) -> bool:
    """Check if the type info looks like TypeScript interfaces."""
    return bool(re.search(r"(?:interface\s+\w+|:\s*(?:string|number|boolean))", type_info))


def _infer_python_schema(type_info: str) -> SchemaInfo:
    """Infer JSON Schema from Python type annotations."""
    # Extract class name
    class_match = re.search(r"class\s+(\w+)", type_info)
    name = class_match.group(1) if class_match else "Unknown"

    properties: dict = {}
    required: list[str] = []

    for match in PYTHON_FIELD_RE.finditer(type_info):
        field_name = match.group(1)
        is_optional = match.group(2) is not None
        field_type = match.group(3)

        type_def = PYTHON_TYPE_MAP.get(field_type, {"type": "string"}).copy()
        if is_optional:
            type_def["nullable"] = True

        properties[field_name] = type_def
        if not is_optional:
            required.append(field_name)

    schema: dict = {
        "type": "object",
        "properties": properties,
    }
    if required:
        schema["required"] = required

    return SchemaInfo(
        name=name,
        schema=schema,
        required_fields=required,
    )


def _infer_typescript_schema(type_info: str) -> SchemaInfo:
    """Infer JSON Schema from TypeScript interface definitions."""
    # Extract interface name
    iface_match = re.search(r"interface\s+(\w+)", type_info)
    name = iface_match.group(1) if iface_match else "Unknown"

    properties: dict = {}
    required: list[str] = []

    for match in TYPESCRIPT_FIELD_RE.finditer(type_info):
        field_name = match.group(1)
        is_optional = match.group(2) == "?"
        field_type = match.group(3)

        type_def = TYPESCRIPT_TYPE_MAP.get(field_type, {"type": "string"}).copy()
        properties[field_name] = type_def
        if not is_optional:
            required.append(field_name)

    schema: dict = {
        "type": "object",
        "properties": properties,
    }
    if required:
        schema["required"] = required

    return SchemaInfo(
        name=name,
        schema=schema,
        required_fields=required,
    )


def _infer_simple_type(type_info: str) -> SchemaInfo:
    """Infer schema from a simple type string."""
    # Try direct type mapping
    stripped = type_info.strip()
    if stripped in PYTHON_TYPE_MAP:
        return SchemaInfo(
            name=stripped,
            schema=PYTHON_TYPE_MAP[stripped],
        )

    # Default fallback
    return SchemaInfo(
        name="Unknown",
        schema={"type": "string"},
    )


def infer_schema(type_info: str) -> SchemaInfo:
    """Infer JSON Schema from type annotations.

    Supports Python class definitions with type annotations and
    TypeScript interface definitions.

    Args:
        type_info: Type annotation text (Python class or TypeScript interface).

    Returns:
        SchemaInfo with the inferred JSON Schema and required fields.
    """
    if not type_info or not type_info.strip():
        return SchemaInfo(name="Empty", schema={"type": "object"})

    # Check TypeScript first -- interface keyword is unambiguous
    if _is_typescript_type_info(type_info):
        return _infer_typescript_schema(type_info)

    if _is_python_type_info(type_info):
        return _infer_python_schema(type_info)

    return _infer_simple_type(type_info)
