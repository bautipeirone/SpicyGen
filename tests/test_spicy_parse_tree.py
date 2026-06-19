"""Unit tests for Spicy parse tree helpers."""

from src.tools.spicy.parse_tree import (
    FieldArray,
    FieldBitfield,
    FieldString,
    FieldUnit,
    parse_spicy_output,
)


class TestParseTreeLeafCollection:

    def test_field_string_returns_single_leaf_name(self):
        field = FieldString(name="src_port", value="80")

        assert field.get_parse_tree_fields() == ["src_port"]

    def test_collects_leaf_names_from_nested_tree(self):
        tree = FieldUnit(
            name="",
            type="dns::Message",
            value={
                "id": FieldString(name="id", value="42"),
                "flags": FieldBitfield(
                    name="flags",
                    value={
                        "qr": FieldString(name="qr", value="1"),
                        "opcode": FieldString(name="opcode", value="0"),
                    },
                ),
                "answers": FieldArray(
                    name="answers",
                    value=[
                        FieldUnit(
                            name="",
                            type="dns::Answer",
                            value={
                                "name": FieldString(name="name", value="example.com"),
                                "ttl": FieldString(name="ttl", value="300"),
                            },
                        ),
                        FieldString(name="trailing", value="yes"),
                    ],
                ),
            },
        )

        assert tree.get_parse_tree_fields() == [
            "id",
            "qr",
            "opcode",
            "name",
            "ttl",
            "trailing",
        ]

    def test_collects_leaf_names_from_parsed_spicy_output(self):
        parsed = parse_spicy_output(
            """dns::Message {
  id: 42
  flags: {
    qr: 1
    opcode: 0
  }
  answers: [
    dns::Answer {
      name: example.com
      ttl: 300
    }
    ok
  ]
}
"""
        )

        assert parsed.get_parse_tree_fields() == [
            "id",
            "qr",
            "opcode",
            "name",
            "ttl",
            "",
        ]
