"""What reaches a TTS is sayable: a ``<say>`` tag's body cleaned into text a voice can say.

A model sometimes writes what no voice can say — ellipsis runs over blank lines, tag fragments,
Markdown, emoji — and a TTS speaks it as noise (``tts-engine``'s sentence-by-sentence engines used to
send ``Sorry… …`` as ``Sorry…`` then a lone ``…``). ``speakable`` is what both voices hand their
TTS: the tag's body, cleaned as it streams, or ``None`` when nothing sayable is left. Body-agnostic
and pure: no TTS, no robot. See specs/embodiment.md, "What reaches a TTS is sayable".
"""

from __future__ import annotations

import logging
import re
import unicodedata
from collections.abc import AsyncIterator

from wica import OutputStream

logger = logging.getLogger(__name__)

_PUNCTUATION = ".!?…,;:"
_PUNCTUATION_GROUP = rf"[{re.escape(_PUNCTUATION)}]+"
# A Markdown link keeps its text.
_LINK = re.compile(r"\[([^\[\]]{0,200})\]\([^()\s]{0,500}\)")
# An HTML/XML-like tag, whole: `<b>`, `</say>`, `<br/>`, `<!-- … -->`, `<?xml … ?>`.
_TAG = re.compile(r"</?[A-Za-z][\w:.-]*(?:\s[^<>]{0,64})?/?>|<[!?][^<>]{0,64}>")
# What is left of markup: a run of `<`, `>`, `/`, `?`, `!` holding a `<` or `>` (`/?>`, `/><`, `</`).
_MARKERS = re.compile(r"[/?!]*[<>][<>/?!]*")
_MARKDOWN = re.compile(r"[*`#~|\\_]+")
_WHITESPACE = re.compile(r"\s+")
_SPACE_BEFORE_MARK = re.compile(r" (?=[.!?,;:])")
# Punctuation groups separated by whitespace keep the first: `Sorry… … ...` → `Sorry…`.
_PUNCTUATION_RUN = re.compile(rf"({_PUNCTUATION_GROUP})(?:\s+{_PUNCTUATION_GROUP})+")
# Inside a group, a mix of ellipses and two or more dots is one ellipsis.
_DOTS = re.compile(r"[.…]*…[.…]*|\.{2,}")
_LEAD = re.compile(rf"^[\s{re.escape(_PUNCTUATION)}]+")
# A tag the utterance ended inside of (`Hello</say`): dropped.
_UNCLOSED_TAG = re.compile(r"<[/!?]?[A-Za-z][^<>]{0,64}")
# What may still become a tag or a link once more text arrives: held back until it can't.
_OPEN_TAG = re.compile(r"<[/!?]?[^<>]{0,64}")
_OPEN_LINK = re.compile(r"\[[^\[\]]{0,200}(?:\](?:\([^()\s]{0,500})?)?")


def _sayable_char(char: str) -> bool:
    """Whether *char* has something to say: control, format (zero-width, joiners), private-use and
    surrogate characters, combining enclosures, variation selectors and symbols (emoji,
    pictographs) do not — the degree sign does."""
    if char.isspace() or char == "°":
        return True
    if unicodedata.category(char) in ("Cc", "Cf", "Co", "Cs", "Me", "So", "Sk"):
        return False
    code = ord(char)
    return not (0xFE00 <= code <= 0xFE0F or 0xE0100 <= code <= 0xE01EF)


def _clean(chunk: str, *, first: bool) -> str:
    """*chunk* as sayable text; *first* when nothing of the utterance has been passed on yet, so
    the punctuation and whitespace before its first words go too."""
    text = "".join(c for c in unicodedata.normalize("NFKC", chunk) if _sayable_char(c))
    text = _LINK.sub(r"\1", text)
    text = _TAG.sub(" ", text)
    text = _MARKERS.sub(" ", text)
    text = _MARKDOWN.sub(" ", text)
    text = _WHITESPACE.sub(" ", text)
    text = _PUNCTUATION_RUN.sub(r"\1", text)
    text = _SPACE_BEFORE_MARK.sub("", text)
    text = _DOTS.sub("…", text)
    return _LEAD.sub("", text) if first else text


class SayableText:
    """The streaming cleaner: ``feed`` each piece as it is written, ``end`` once the tag closes.
    A piece is passed on up to its last letter or digit; the run after it — whitespace,
    punctuation, markup, an unclosed tag or link — is held until the next word or the end, so no
    pattern ever spans what was passed on and what was held: the output is the same however the
    text was split."""

    def __init__(self) -> None:
        self._held = ""
        self._first = True

    def feed(self, piece: str) -> str:
        self._held += piece
        limit = len(self._held)
        for opener, pattern in (("<", _OPEN_TAG), ("[", _OPEN_LINK)):
            start = self._held.rfind(opener)
            if start != -1 and pattern.fullmatch(self._held, start):
                limit = min(limit, start)
        cut = self._cut(limit)
        if not cut:
            return ""
        return self._emit(self._held[:cut], self._held[cut:])

    def end(self) -> str:
        start = self._held.rfind("<")
        if start != -1 and _UNCLOSED_TAG.fullmatch(self._held, start):
            self._held = self._held[:start]
        return self._emit(self._held, "").rstrip()

    def _cut(self, limit: int) -> int:
        """Just after the last letter or digit before *limit* that is not inside a complete tag
        or link (those are removed whole), or 0."""
        spans = [
            m.span() for p in (_TAG, _LINK) for m in p.finditer(self._held, 0, limit)
        ]
        cut = limit
        while True:
            while cut and not self._held[cut - 1].isalnum():
                cut -= 1
            inside = [start for start, end in spans if start < cut < end]
            if not inside:
                return cut
            cut = min(inside)

    def _emit(self, chunk: str, rest: str) -> str:
        self._held = rest
        cleaned = _clean(chunk, first=self._first)
        if cleaned:
            self._first = False
        return cleaned


async def speakable(text: OutputStream) -> AsyncIterator[str] | None:
    """*text* cleaned into sayable text, as a stream a TTS can take, once it holds something to
    say: ``None`` when it ends with no letter or digit left, so an empty utterance never reaches
    the engine or the robot.

    Waits only for the first words, then returns a stream that starts with them, so speech still
    starts early (specs/embodiment.md, "What reaches a TTS is sayable")."""
    pieces = text.__aiter__()
    cleaner = SayableText()
    written: list[str] = []
    said: list[str] = []
    async for piece in pieces:
        written.append(piece)
        if words := cleaner.feed(piece):
            said.append(words)
            break
    else:
        if words := cleaner.end():
            said.append(words)
        if not any(c.isalnum() for c in "".join(said)):
            _log_cleaning(written, said)
            return None

    from_lead = "".join(said)

    async def stream() -> AsyncIterator[str]:
        yield from_lead
        async for piece in pieces:
            written.append(piece)
            if words := cleaner.feed(piece):
                said.append(words)
                yield words
        if words := cleaner.end():
            said.append(words)
            yield words
        _log_cleaning(written, said)

    return stream()


def _log_cleaning(written: list[str], said: list[str]) -> None:
    """Log what the model wrote next to what was said, when the cleaning changed it."""
    before, after = "".join(written), "".join(said)
    if after != before.strip():
        logger.debug("say: cleaned %r into %r", before, after)
