#!/usr/bin/bash

MODELS="qwen3-coder gemini-3.1-flash"
PROTOCOLS="tftp dhcp http dns"

BASE_OUTPUT_PATH="$PWD/outputs"
MODEL_INDEX_FILE="model_index.json"

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
  local MODEL=$1
  PROVIDER=$(jq -r --arg MODEL "$MODEL" '.[$MODEL].provider' < "$MODEL_INDEX_FILE")
  if [ "$PROVIDER" = "null" ]; then
    echo "Null provider" 1>&2
    exit 1
  fi
  echo "$PROVIDER"
}

get_model_name() {
  local MODEL=$1
  MODEL_NAME=$(jq -r --arg MODEL "$MODEL" '.[$MODEL].name' < "$MODEL_INDEX_FILE")
  if [ "$MODEL_NAME" = "null" ]; then
    echo "Model name not found" 1>&2
    exit 1
  fi
  echo "$MODEL_NAME"
}

# First input is model id (from model_index.json)
# Second input is protocol
run_grammar_generator() {
  local MODEL_ID=$1
  local PROTOCOL=$2

  MODEL_NAME=$(get_model_name "$MODEL_ID")

  OUTPUT_PATH="$RUN_OUT_DIR/$PROTOCOL-$MODEL_NAME.spicy"
  
  local DEBUG_ARGS=""
  if [[ -n "$DEBUG_DIR" ]]; then
    DEBUG_ARGS="--debug-dir $DEBUG_DIR/${PROTOCOL}-${MODEL_NAME}"
  fi

  local METRICS_PATH="${OUTPUT_PATH%.spicy}.json"
  python generate_grammar.py -m "$MODEL_ID" -o "$OUTPUT_PATH" $CONFIG_ARGS $DEBUG_ARGS --metrics-file "$METRICS_PATH" "$PROTOCOL"
  if [ $? -ne 0 ]; then
    echo "GRAMMAR PRODUCED BY $MODEL_ID DIDNT PASS TESTS: $(basename "$OUTPUT_PATH")"
  else
    echo "GRAMMAR PRODUCED BY $MODEL_ID PASSED TESTS: $(basename "$OUTPUT_PATH")"
  fi
}

############ INITIALIZATION #############
source "$PWD/../venv/bin/activate"

TIMESTAMP=$(date +"%Y%m%d_%H%M%S")
RUN_OUT_DIR="$BASE_OUTPUT_PATH/$TIMESTAMP"
mkdir -p "$RUN_OUT_DIR"
if [[ -n "$DEBUG_MODE" ]]; then
  DEBUG_DIR="$RUN_OUT_DIR/debug"
  mkdir "$DEBUG_DIR"
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
    run_grammar_generator "$m" "$p"
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
