import pytest

from tilik_agent.chat.storage import conversation_title_from_question


@pytest.mark.parametrize(
    ("question", "expected"),
    [
        ("berapa TPT Papua", "berapa TPT Papua"),
        ("  berapa   TPT  ", "berapa TPT"),
    ],
)
def test_conversation_title_keeps_a_short_question(question: str, expected: str) -> None:
    """
    Condition:
    The question is shorter than the title limit, including extra whitespace.

    Expected:
    The title is the question with whitespace collapsed, and no ellipsis.
    """
    assert conversation_title_from_question(question) == expected


def test_conversation_title_breaks_on_a_word() -> None:
    """
    Condition:
    The question is longer than the requested limit and contains spaces.

    Expected:
    The title stops on a word boundary and ends with an ellipsis, without cutting mid-word.
    """
    question = "Berapa selisih TPT Provinsi Jawa Barat antara Februari dan Agustus"
    title = conversation_title_from_question(question, max_len=32)
    assert title.endswith("…")
    assert " " in title
    assert not title[:-1].endswith(" ")
    assert question.startswith(title[:-1])
