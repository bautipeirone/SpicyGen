#!/usr/bin/bash

MODELS="ollama/qwen3-coder:480b-cloud google/gemini-3.1-flash-lite-preview"
PROTOCOLS="tftp dhcp http dns"

BASE_RFC_PATH=$SPEC_EXTRACT/rfc
BASE_OUTPUT_PATH=$SPEC_EXTRACT/code/spicy/llm-results

DEBUG_MODE=""
POSITIONAL_ARGS=()

usage () {
  echo "Usage: $0 [options]"
  echo "Options:"
  echo "  -m|--models <models> Comma separated list of models to use"
  echo "  -p|--protocols <protocols> Comma separated list of protocols to use"
  echo "  --debug             Enable debug mode"
  echo "  --rfc-dir <path>    Path to the rfc directory"
  echo "  --output-dir <path> Path to the output directory"
  echo "  --note <text>       Add a note for this run"
  echo "  -h|--help           Show this help message"
}

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
    -c|--config)
      CONFIG_PATH="$2"
      shift
      shift
      ;;
    --note)
      RUN_NOTE="$2"
      shift
      shift
      ;;
    --debug)
      DEBUG_MODE="1"
      shift
      ;;
    -h|--help)
      usage
      exit 0
      ;;
    -*)
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

get_provider() {
  echo "${1%/*}"
}

get_model_name() {
  echo "${1#*/}"
}

# First input is model
# Second input is rfc
# Third input is output file
# Fourth input is protocol
# Fifth is debug dir (optional)
run_grammar_generator() {
  local PROVIDER
  local MODEL
  local RFC_PATH=$2
  local OUTPUT_PATH=$3
  local PROTOCOL=$4
  local DEBUG_DIR=$5
  PROVIDER=$(get_provider "$1")
  MODEL=$(get_model_name "$1")
  
  local DEBUG_ARGS=""
  if [[ -n "$DEBUG_DIR" ]]; then
    DEBUG_ARGS="--debug-dir $DEBUG_DIR/${PROTOCOL}-${MODEL}/refinement"
  fi

  python generate_grammar.py -p "$PROVIDER" -m "$MODEL" -o "$OUTPUT_PATH" $CONFIG_ARGS $DEBUG_ARGS "$RFC_PATH"
  if spicyc -j "$OUTPUT_PATH" 2> /dev/null; then
    echo "GRAMMAR PRODUCED BY $MODEL COMPILED SUCCESFULLY: $(basename "$OUTPUT_PATH")"
  else
    echo "GRAMMAR PRODUCED BY $MODEL DIDNT COMPILE: $(basename "$OUTPUT_PATH")"
  fi
}

############ INITIALIZATION #############
source "$SPEC_EXTRACT/code/venv/bin/activate"

TIMESTAMP=$(date +"%Y%m%d_%H%M%S")
RUN_OUT_DIR="$BASE_OUTPUT_PATH/$TIMESTAMP"
mkdir -p "$RUN_OUT_DIR"
if [[ -n "$DEBUG_MODE" ]]; then
  DEBUG_DIR="debug/$TIMESTAMP"
  mkdir -p "$DEBUG_DIR"
fi

if [[ -n "$CONFIG_PATH" ]]; then
  CONFIG_ARGS="--config $CONFIG_PATH"
fi

cat <<EOF > "$RUN_OUT_DIR/README.md"
# Run Details
- **Timestamp:** $TIMESTAMP
- **Models:** $MODELS
- **Protocols:** $PROTOCOLS

## Note
${RUN_NOTE:-"No note provided."}
EOF
#########################################

for p in $PROTOCOLS; do
  for m in $MODELS; do
    RFC_PATH=$(get_rfc "$p")
    OUTPUT_PATH="$RUN_OUT_DIR/$p-$(get_model_name "$m").spicy"
    run_grammar_generator "$m" "$RFC_PATH" "$OUTPUT_PATH" "$p" "$DEBUG_DIR"
  done
done
