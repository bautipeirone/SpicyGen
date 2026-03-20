# Spicy notes
This repository provides functionality for using LLMs to generate spicy grammars
for protocols specifications from their RFCs.

## Usage
Install python dependencies with `pip install -r requirements.txt`. Then, run 
```bash
python generate_grammar.py --provider PROVIDER --model MODEL -o OUTPUT_FILE <rfc_file>
```
Provider is the LLM client that hosts the model. It can be one of the following: ollama, google, openai. Necessary API keys required by each one have to be set.

Another possibility to test many models along various protocols is to use the shell script.
```bash
./benchmark.sh -m "ollama/qwen3-vl:235b-instruct-cloud ollama/gpt-oss:120b-cloud google/gemini-3-flash-preview"
               -p "tftp dhcp http" --rfc-dir path_to_rfc_dir --output-dir path_to_output
```
This will look for RFCs with the pattern `path_to_rfc_dir/*-rfc.txt` and input it to each one of the models specified. It will also
store the result and try to compile it with `spicyc` to test if the LLM produced a reasonable response or not.

## Spicy CLI Tools
* `spicyc -c <file>`: output C++ code for file
* `spicyc -j <file>`: generate and run parser
* `spicyc -j -o output.hlto <file>`: precompile to object code
* `spicy-build -o output <file>`: generate parser executable
* `spicy-driver <file>`: read from stdin and inputs it to the parser.
  Run with `-d` flag to avoid the compiler from optimizing out fields.
* `spicy-dump <file>`: like `spicy-driver`, but outputs in pretty format.
  With `-J` it outputs JSON.

## Useful extra features
The following features are not currently included in the `INSTRUCTIONS.md` prompt,
but are written down here for future reference in case they are needed.
* Contexts: allows for use of shared context between parsing parsers. This could
  be used for request-response like protocol messages, which tend to share some
  common values. 
* Filters
* On-the-fly Type Conversion
* Hooks
