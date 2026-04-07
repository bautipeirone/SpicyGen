#!/usr/bin/bash

INPUT_FILE=$1
OUTPUT_FILE=$2

usage () {
  echo "Usage: pcap_to_batch.sh <pcap_path> <output_path>"
}

if ! [[ $# = 2 ]]; then
  usage
  exit 0
fi

zeek -r "$INPUT_FILE" policy/frameworks/spicy/record-spicy-batch SpicyBatch::filename="$OUTPUT_FILE"