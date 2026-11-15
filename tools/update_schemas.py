import json
from pathlib import Path

from trueparse.contracts.schema import SchemaName, build_schema


def main():
    destination = Path(__file__).resolve().parents[1] / "src/trueparse/contracts/schemas"
    names: tuple[SchemaName, ...] = ("document", "request", "response", "chunk")
    for name in names:
        (destination / f"{name}-3.0.json").write_text(
            json.dumps(build_schema(name), indent=2) + "\n", encoding="utf-8"
        )


if __name__ == "__main__":
    main()
