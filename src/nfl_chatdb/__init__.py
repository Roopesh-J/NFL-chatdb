"""NFL chat-with-your-database: two-stage NL-to-SQL pipeline."""

from nfl_chatdb.client import build_client
from nfl_chatdb.database import connect
from nfl_chatdb.pipeline import PipelineOutcome, answer_question
from nfl_chatdb.schema import load_schema_text

__version__ = "0.1.0"

__all__ = [
    "answer_question",
    "PipelineOutcome",
    "build_client",
    "connect",
    "load_schema_text",
]
