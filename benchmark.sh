#!/usr/bin/bash

MODELS="qwen3.5:cloud gpt-oss:120b-cloud"
PROTOCOLS="tftp dhcp http dns"

BASE_RFC_PATH=$SPEC_EXTRACT/rfc
BASE_OUTPUT_PATH=$SPEC_EXTRACT/code/spicy/llm-results

POSITIONAL_ARGS=()

while [[ $# -gt 0 ]]; do
  case $1 in
    --rfc-dir)
      BASE_RFC_PATH="$2"
      shift
      shift
      ;;
    --output-dir)
      BASE_OUTPUT_PATH="$2"
      shift
      shift
      ;;
    -m|--models)
      MODELS="$2"
      shift
      shift
      ;;
    -p|--protocols)
      PROTOCOLS="$2"
      shift
      shift
      ;;
    -*|--*)
      echo "Unknown option $1"
      exit 1
      ;;
    *)
      POSITIONAL_ARGS+=("$1")
      shift
      ;;
  esac
done

set -- "${POSITIONAL_ARGS[@]}"

get_rfc() {
  echo "$BASE_RFC_PATH/$1-rfc.txt"
}

# First input is model
# Second input is rfc
# Third input is output file
run_grammar_generator() {
  local MODEL=$1
  local RFC_PATH=$2
  local OUTPUT_PATH=$3
  python get_spicy_grammar.py -m $MODEL -o $OUTPUT_PATH $RFC_PATH
  spicyc -j $OUTPUT_PATH 2> /dev/null
  if [[ $? -eq 0 ]]; then
    echo "GRAMMAR PRODUCED BY $MODEL COMPILED SUCCESFULLY: $(basename $OUTPUT_PATH)"
  else
    echo "GRAMMAR PRODUCED BY $MODEL DIDNT COMPILE: $(basename $OUTPUT_PATH)"
  fi
}

############ INITIALIZATION #############
source $SPEC_EXTRACT/code/venv/bin/activate
#########################################

for p in $PROTOCOLS; do
  for m in $MODELS; do
    RFC_PATH=$(get_rfc $p)
    OUTPUT_PATH="$BASE_OUTPUT_PATH/$p-$m.spicy"
    run_grammar_generator $m $RFC_PATH $OUTPUT_PATH &
  done
done

wait