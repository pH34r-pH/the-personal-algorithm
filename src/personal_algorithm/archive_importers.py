"""Implemented archive parsers shared by the API and onboarding UI."""

from .reddit_import import import_reddit_history
from .spotify_import import import_spotify_history

ARCHIVE_IMPORTERS = {
    "spotify": import_spotify_history,
    "reddit": import_reddit_history,
}

IMPORT_COVERAGE = {
    "spotify": "Extended Streaming History only; other files remain retained",
    "reddit": "Posts and comments CSVs only; other files remain retained",
}
