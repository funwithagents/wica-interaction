"""`speakable`: what reaches a TTS is sayable (specs/embodiment.md, "What reaches a TTS is
sayable"). Every case is fed whole, one character at a time and in pieces of four: the text the
TTS receives must not depend on how the model's text was split."""

from __future__ import annotations

import asyncio

import pytest

from tests.fakes import utterance
from wica_interaction.embodiment import speakable

SPLITS = (0, 1, 4)


def _pieces(text: str, size: int) -> list[str]:
    return (
        [text] if size == 0 else [text[i : i + size] for i in range(0, len(text), size)]
    )


async def _said(*pieces: str) -> str | None:
    words = await speakable(utterance(*pieces))
    if words is None:
        return None
    return "".join([piece async for piece in words])


def said(text: str) -> str | None:
    """What a TTS receives for *text*, the same under every split (asserted)."""
    results = {asyncio.run(_said(*_pieces(text, size))) for size in SPLITS}
    assert len(results) == 1, f"depends on the split: {results}"
    return results.pop()


@pytest.mark.parametrize(
    ("written", "spoken"),
    [
        # What models were seen writing, heard as noise.
        ("Sorry…    …   \nWe      …  \n   … …  \n  …  \n  …  \n ...", "Sorry… We…"),
        ("Welcome           …      ...…………\n... … …\n", "Welcome…"),
        ("Sorry… …", "Sorry…"),
        ("Sorry… ...", "Sorry…"),
        ("Sorry... ...", "Sorry…"),
        ("Hi. … Bye", "Hi. Bye"),
        ("Hello there /?> nice />< to see you", "Hello there nice to see you"),
        ("Hello</say", "Hello"),
    ],
)
def test_garbage_comes_out_as_sayable_text(written: str, spoken: str) -> None:
    assert said(written) == spoken


@pytest.mark.parametrize(
    "written",
    ["Sorry…", "Sorry… I didn't catch that.", "What?! Really, now; fine: ok."],
)
def test_sayable_text_is_left_as_written(written: str) -> None:
    assert said(written) == written


def test_a_single_ellipsis_is_kept_however_written() -> None:
    assert said("Sorry ...") == "Sorry…"
    assert said("Sorry …") == "Sorry…"


def test_numbers_and_decimals_survive() -> None:
    assert said("It is 3.14, not 3.15.") == "It is 3.14, not 3.15."


def test_markup_is_removed_and_its_words_kept() -> None:
    assert (
        said("**Bold** and `code`, see [the docs](https://example.org/a) <b>now</b>!")
        == "Bold and code, see the docs now!"
    )
    assert said("# Title\n- one\n- two") == "Title - one - two"


def test_a_lone_angle_bracket_between_words_goes() -> None:
    assert said("a < b and c > d") == "a b and c d"


def test_emoji_and_invisible_characters_go_but_degrees_and_accents_stay() -> None:
    assert (
        said("Bravo 👍🏽 il fait 20°C au café\u200b !") == "Bravo il fait 20°C au café!"
    )


@pytest.mark.parametrize(
    "written", ["", "   \n", "… … ...", "<b></b>", "😀 🎉", "/?> />", "**", "</say"]
)
def test_nothing_sayable_is_none(written: str) -> None:
    assert said(written) is None


def test_the_first_words_pass_before_the_tag_closes() -> None:
    """Speech still starts early: the first words are passed on while the model is still
    writing, and what may still turn into garbage (a run of ellipses) is held, not spoken."""

    async def scenario() -> tuple[str, str]:
        text = utterance("Hello there … ", close=False)
        words = await asyncio.wait_for(speakable(text), timeout=1.0)
        assert words is not None
        first = await asyncio.wait_for(anext(words), timeout=1.0)
        text.push("…\n… friend.")
        text.close()
        rest = "".join([piece async for piece in words])
        return first, rest

    first, rest = asyncio.run(scenario())
    assert first == "Hello there"
    assert first + rest == "Hello there… friend."


def test_an_unclosed_tag_is_held_until_it_closes() -> None:
    async def scenario() -> tuple[str, str]:
        text = utterance("Hi there <", close=False)
        words = await asyncio.wait_for(speakable(text), timeout=1.0)
        assert words is not None
        first = await asyncio.wait_for(anext(words), timeout=1.0)
        text.push("br/>friend")
        text.close()
        return first, "".join([piece async for piece in words])

    first, rest = asyncio.run(scenario())
    assert first == "Hi there"
    assert rest == " friend"
