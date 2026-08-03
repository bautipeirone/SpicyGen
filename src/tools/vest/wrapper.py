from pathlib import Path

MAIN_WRAPPER_SRC = r"""
use std::env;
use std::fs;
use std::path::Path;
use std::process::ExitCode;

pub mod {module_name};

use {module_name}::{parser_entrypoint};

fn main() -> ExitCode {{
    let mut args = env::args_os();
    let program = args
        .next()
        .unwrap_or_else(|| "dhcp-parser".into());

    let input_path = match args.next() {{
        Some(path) => path,
        None => {
            eprintln!(
                "usage: {{}} <input-file>",
                Path::new(&program)
                    .file_name()
                    .and_then(|s| s.to_str())
                    .unwrap_or("dhcp-parser")
            );
            return ExitCode::from(1);
        }}
    }};

    if args.next().is_some() {{
        eprintln!("error: expected exactly one input file");
        return ExitCode::from(1);
    }}

    let input = match fs::read(&input_path) {{
        Ok(bytes) => bytes,
        Err(err) => {{
            eprintln!("error: failed to read input file: {{err}}");
            return ExitCode::from(1);
        }}
    }};

    match {parser_entrypoint}(&input) {{
        Ok((consumed, _packet)) => {{
            if consumed == input.len() {{
                ExitCode::from(0)
            }} else {{
                eprintln!(
                    "error: parser succeeded but consumed only {{}} of {{}} bytes",
                    consumed,
                    input.len()
                );
                ExitCode::from(1)
            }}
        }}

        Err(err) => {{
            eprintln!("parse error: {{err:!?}}");
            ExitCode::from(1)
        }}
    }}
}}
"""


def get_main_src(module_name: str, parser_entrypoint: str) -> str:
    return MAIN_WRAPPER_SRC.format(module_name=module_name, parser_entrypoint=parser_entrypoint)


def write_main(file: Path, out: Path):
    grammar_code = file.read_text()
    module_name = ""
    parser_entrypoint = ""
    main_src = get_main_src(module_name, parser_entrypoint)
    out.write_text(main_src)
