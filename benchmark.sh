#!/usr/bin/bash

MODELS="qwen3-coder gemini-3.1-flash"
PROTOCOLS="tftp dhcp http dns"
TARGET_LANGUAGES="spicy"

BASE_OUTPUT_PATH="$PWD/outputs"
MODEL_INDEX_FILE="model_index.json"

DEBUG_MODE=""
SPEC_FILE=""
POSITIONAL_ARGS=()

usage () {
  echo "Usage: $0 [options]"
  echo "Options:"
  echo "  -m|--models <models> Comma separated list of models to use"
  echo "  -p|--protocols <protocols> Comma separated list of protocols to use"
  echo "  --debug             Enable debug mode"
  echo "  --spec-file <path>  Override specification file to use"
  echo "  -t|--target-languages <languages> Target languages/toolchain to use"
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
    --spec-file)
      SPEC_FILE="$2"
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
    -t|--target-languages)
      TARGET_LANGUAGES="$2"
      shift
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

get_target_language_extension() {
  local LANGUAGE=$1
  python -c "from src.target_language import get_target_language; print(get_target_language('$LANGUAGE').source_extension)"
}

# First input is model id (from model_index.json)
# Second input is protocol
run_grammar_generator() {
  local MODEL_ID=$1
  local PROTOCOL=$2
  local TARGET_LANGUAGE=$3

  MODEL_NAME=$(get_model_name "$MODEL_ID")
  OUTPUT_EXT=$(get_target_language_extension "$TARGET_LANGUAGE")

  OUTPUT_PATH="$RUN_OUT_DIR/$PROTOCOL-$MODEL_NAME$OUTPUT_EXT"
  
  local DEBUG_ARGS=""
  if [[ -n "$DEBUG_DIR" ]]; then
    DEBUG_ARGS="--debug-dir $DEBUG_DIR/${PROTOCOL}-${MODEL_NAME}-${TARGET_LANGUAGE}"
  fi

  local SPEC_ARGS=""
  if [[ -n "$SPEC_FILE" ]]; then
    SPEC_ARGS="--spec-file $SPEC_FILE"
  fi

  local METRICS_PATH="${OUTPUT_PATH%$OUTPUT_EXT}-${TARGET_LANGUAGE}.json"
  python generate_grammar.py -m "$MODEL_ID" -t "$TARGET_LANGUAGE" -o "$OUTPUT_PATH" $SPEC_ARGS $CONFIG_ARGS $DEBUG_ARGS --metrics-file "$METRICS_PATH" "$PROTOCOL"
  if [ $? -ne 0 ]; then
    echo "GRAMMAR PRODUCED BY $MODEL_ID DIDNT PASS TESTS: $(basename "$OUTPUT_PATH")"
  else
    echo "GRAMMAR PRODUCED BY $MODEL_ID PASSED TESTS: $(basename "$OUTPUT_PATH")"
  fi
}

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
- **Target languages:** $TARGET_LANGUAGES

## Note
${RUN_NOTE:-"No note provided."}
EOF
#########################################

for p in $PROTOCOLS; do
  for m in $MODELS; do
    for t in $TARGET_LANGUAGES; do
      run_grammar_generator "$m" "$p" "$t"
    done
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
