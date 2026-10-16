from pathlib import Path

# Side effect at import/run time: ingestion must never trigger this.
Path("EXECUTED_MARKER").write_text("ran")


def main() -> None:
    print("hello")
