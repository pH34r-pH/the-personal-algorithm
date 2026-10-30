"""Implemented archive parsers shared by the API and onboarding UI."""

from .message_import import import_message_archive
from .reddit_import import import_reddit_history
from .spotify_import import import_spotify_history

ARCHIVE_IMPORTERS = {
    "spotify": import_spotify_history,
    "reddit": import_reddit_history,
    "messages": import_message_archive,
}

IMPORT_COVERAGE = {
    "spotify": "Extended Streaming History only; other files remain retained",
    "reddit": "Posts and comments CSVs only; other files remain retained",
    "messages": "Explicitly sent messages in structured JSONL only",
}
