"""pywebview desktop app wrapping the two-stage NL-to-SQL pipeline."""

from __future__ import annotations

import argparse
from pathlib import Path

import anthropic

from nfl_chatdb.cli import build_client
from nfl_chatdb.database import DEFAULT_DB_PATH, QueryError, connect
from nfl_chatdb.formatting import outcome_to_dict
from nfl_chatdb.pipeline import answer_question
from nfl_chatdb.schema import load_schema_text
from nfl_chatdb.stage1_sql import Stage1Error

_INDEX_HTML = Path(__file__).parent / "web" / "index.html"


class Api:
    """The object exposed to the page via pywebview's ``js_api`` bridge."""

    def __init__(self, client, schema_text: str, db_path=DEFAULT_DB_PATH):
        self._client = client
        self._schema_text = schema_text
        self._db_path = db_path

    def ask(self, question: str) -> dict:
        question = (question or "").strip()
        if not question:
            return {"error": "Enter a question."}

        try:
            conn = connect(self._db_path)
        except QueryError as err:
            return {"error": str(err)}

        try:
            outcome = answer_question(
                self._client,
                question,
                conn=conn,
                schema_text=self._schema_text,
            )
            return outcome_to_dict(outcome)
        except Stage1Error as err:
            return {
                "error": f"Could not produce a working query: {err.last_error}"
            }
        except anthropic.APIError as err:
            return {"error": f"Anthropic API call failed: {err}"}
        except QueryError as err:
            return {"error": str(err)}
        finally:
            conn.close()


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(
        description="Desktop app for asking natural-language questions "
        "about NFL statistics."
    )
    parser.add_argument("--db", default=None, help="Path to the SQLite database.")
    args = parser.parse_args(argv)
    db_path = Path(args.db) if args.db else DEFAULT_DB_PATH

    try:
        schema_text = load_schema_text()
    except FileNotFoundError as err:
        print(f"{err}\nRun `uv run python -m nfl_chatdb.ingest` first.")
        return 2

    try:
        client = build_client()
    except anthropic.AnthropicError as err:
        print(
            f"Could not create the Anthropic client: {err}\n"
            "Set ANTHROPIC_API_KEY (for example in a .env file)."
        )
        return 2

    try:
        connect(db_path).close()
    except QueryError as err:
        print(f"{err}\nRun `uv run python -m nfl_chatdb.ingest` first.")
        return 2

    import webview

    api = Api(client, schema_text, db_path)
    webview.create_window(
        "NFL ChatDB",
        url=str(_INDEX_HTML),
        js_api=api,
        width=900,
        height=700,
    )
    webview.start()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
