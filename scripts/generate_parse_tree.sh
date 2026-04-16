#!/usr/bin/bash

GRAMMAR_PATH=$1
PACKET_PATH=$2

get_parse_tree() {
  local packet=$1
  PARSE_TREE_FILE="${packet%.bin}.json"
  spicy-dump -J "$GRAMMAR_PATH" < "$packet" | jq > "$PARSE_TREE_FILE" 
}

if [[ -f "$PACKET_PATH" ]]; then
  get_parse_tree "$PACKET_PATH"
fi
