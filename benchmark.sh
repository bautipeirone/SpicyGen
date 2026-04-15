#!/usr/bin/bash

MODELS="ollama/qwen3-coder:480b-cloud google/gemini-3.1-flash-lite-preview"
PROTOCOLS="tftp dhcp http dns"

BASE_OUTPUT_PATH="$PWD/llm-results"

DEBUG_MODE=""
POSITIONAL_ARGS=()

usage () {
  echo "Usage: $0 [options]"
  echo "Options:"
  echo "  -m|--models <models> Comma separated list of models to use"
  echo "  -p|--protocols <protocols> Comma separated list of protocols to use"
  echo "  --debug             Enable debug mode"
  echo "  --output-dir <path> Path to the output directory"
  echo "  --note <text>       Add a note for this run"
  echo "  -h|--help           Show this help message"
}

while [[ $# -gt 0 ]]; do
  case $1 in
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
  local OUTPUT_PATH=$2
  local PROTOCOL=$3
  local DEBUG_DIR=$4
  PROVIDER=$(get_provider "$1")
  MODEL=$(get_model_name "$1")
  
  local DEBUG_ARGS=""
  if [[ -n "$DEBUG_DIR" ]]; then
    DEBUG_ARGS="--debug-dir $DEBUG_DIR/${PROTOCOL}-${MODEL}"
  fi

  local METRICS_PATH="${OUTPUT_PATH%.spicy}.json"
  python generate_grammar.py -p "$PROVIDER" -m "$MODEL" -o "$OUTPUT_PATH" $CONFIG_ARGS $DEBUG_ARGS --metrics-file "$METRICS_PATH" "$PROTOCOL"
  if spicyc -j "$OUTPUT_PATH" 2> /dev/null; then
    echo "GRAMMAR PRODUCED BY $MODEL COMPILED SUCCESFULLY: $(basename "$OUTPUT_PATH")"
  else
    echo "GRAMMAR PRODUCED BY $MODEL DIDNT COMPILE: $(basename "$OUTPUT_PATH")"
  fi
}

############ INITIALIZATION #############
source "$PWD/../venv/bin/activate"

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
    OUTPUT_PATH="$RUN_OUT_DIR/$p-$(get_model_name "$m").spicy"
    run_grammar_generator "$m" "$OUTPUT_PATH" "$p" "$DEBUG_DIR"
  done
done

# Aggregate individual JSON metrics into a single JSONL report
REPORT_FILE="$RUN_OUT_DIR/report.jsonl"
for f in "$RUN_OUT_DIR"/*.json; do
  if [ -f "$f" ]; then
    cat "$f" >> "$REPORT_FILE"
    echo "" >> "$REPORT_FILE"
    rm "$f"
  fi
done
