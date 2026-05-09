"""Tests for agency Markdown frontmatter parser."""

import pytest

from agent_nexus.platform.agency.parser import parse_frontmatter


class TestParseFrontmatter:
    def test_valid_frontmatter(self):
        md = "---\nname: test-agent\ndescription: A test agent\n---\nBody content"
        result = parse_frontmatter(md)
        assert result["name"] == "test-agent"
        assert result["description"] == "A test agent"
        assert result["body"] == "Body content"

    def test_all_fields(self):
        md = "---\nname: full\nfoo: bar\n---\n# Content"
        result = parse_frontmatter(md)
        assert result["name"] == "full"
        assert result["body"] == "# Content"

    def test_extra_yaml_fields(self):
        md = "---\nname: agent\nkey: value\n---\nbody"
        result = parse_frontmatter(md)
        assert result["name"] == "agent"

    def test_defaults_for_missing_fields(self):
        md = "---\nname: x\n---\nbody"
        result = parse_frontmatter(md)
        assert result["description"] == ""
        assert result["color"] == ""
        assert result["emoji"] == ""
        assert result["vibe"] == ""

    def test_multiline_body(self):
        md = "---\nname: x\n---\nline1\nline2\nline3"
        result = parse_frontmatter(md)
        assert result["body"] == "line1\nline2\nline3"

    def test_empty_body(self):
        md = "---\nname: x\n---\n"
        result = parse_frontmatter(md)
        assert result["body"] == ""

    def test_whitespace_stripped(self):
        md = "  ---\nname: x\n---\nbody  "
        result = parse_frontmatter(md)
        assert result["name"] == "x"
        assert result["body"] == "body"

    def test_closing_delimiter_with_trailing_spaces(self):
        md = "---\nname: x\n---  \nbody"
        result = parse_frontmatter(md)
        assert result["name"] == "x"


class TestParseFrontmatterErrors:
    def test_no_opening_delimiter(self):
        with pytest.raises(ValueError, match="must start with '---'"):
            parse_frontmatter("name: x\n---\nbody")

    def test_single_delimiter_no_newline(self):
        with pytest.raises(ValueError, match="single '---'"):
            parse_frontmatter("---")

    def test_no_closing_delimiter(self):
        with pytest.raises(ValueError, match="closing"):
            parse_frontmatter("---\nname: x\nbody")

    def test_empty_frontmatter(self):
        with pytest.raises(ValueError, match="Empty frontmatter"):
            parse_frontmatter("---\n---\nbody")

    def test_invalid_yaml(self):
        with pytest.raises(ValueError, match="Invalid YAML"):
            parse_frontmatter("---\n: invalid: yaml: [\n---\nbody")

    def test_non_dict_yaml(self):
        with pytest.raises(ValueError, match="YAML mapping"):
            parse_frontmatter("---\n- item1\n- item2\n---\nbody")

    def test_missing_name(self):
        with pytest.raises(ValueError, match="name"):
            parse_frontmatter("---\ndescription: no name\n---\nbody")

    def test_empty_name(self):
        with pytest.raises(ValueError, match="name"):
            parse_frontmatter("---\nname: ''\n---\nbody")

    def test_whitespace_only_name(self):
        with pytest.raises(ValueError, match="name"):
            parse_frontmatter("---\nname: '   '\n---\nbody")

    def test_yaml_error_chained(self):
        with pytest.raises(ValueError) as exc_info:
            parse_frontmatter("---\n: bad: [\n---\nbody")
        assert exc_info.value.__cause__ is not None
