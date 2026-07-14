"""Turn human-corrected extractions into an Azure OpenAI fine-tuning JSONL file.

Corrections are one JSON file per document in the `corrections` blob container,
written whenever a reviewer fixes an agent extraction:
    {"text": "<ocr text>", "schema": {...}, "output": {...corrected entities...}}

Usage: python ml/prepare_training_data.py corrections/ training_data.jsonl
"""
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from agent.setup_agent import INSTRUCTIONS


def build_example(item: dict) -> dict:
    user_text = (
        f"Extract structured entities from this document text as a single JSON object "
        f"matching exactly this schema: {json.dumps(item['schema'])}.\n\n{item['text']}"
    )
    return {
        "messages": [
            {"role": "system", "content": INSTRUCTIONS},
            {"role": "user", "content": user_text},
            {"role": "assistant", "content": json.dumps(item["output"])},
        ]
    }


def main(corrections_dir: str, output_path: str) -> None:
    examples = [build_example(json.loads(p.read_text())) for p in Path(corrections_dir).glob("*.json")]
    if not examples:
        raise SystemExit(f"No correction files found in {corrections_dir}")

    with open(output_path, "w") as f:
        for example in examples:
            f.write(json.dumps(example) + "\n")
    print(f"Wrote {len(examples)} training examples to {output_path}")


if __name__ == "__main__":
    if len(sys.argv) != 3:
        print(__doc__)
        sys.exit(1)
    main(sys.argv[1], sys.argv[2])
