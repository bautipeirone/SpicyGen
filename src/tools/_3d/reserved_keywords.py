# Spicy list of reserved keywords
RESERVED_KEYWORDS: set[str] = {
  "aligned",
  "case",
  "casetype",
  "const",
  "default",
  "else",
  "entrypoint",
  "enum",
  "if",
  "mutable",
  "pointer",
  "sizeof"
  "struct",
  "switch",
  "type",
  "where",
}

REPLACE_KEYWORDS_MAP: dict[str, str] = {
  "type": "type_value",
}