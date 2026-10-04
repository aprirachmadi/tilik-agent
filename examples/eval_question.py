"""Answer one question and score it with the benchmark judges.

Edit ``QUESTION`` and run ``poetry run python examples/eval_question.py``. Nothing is
uploaded to a Langfuse experiment; the question trace is still sent when Langfuse is configured.
"""

from tilik_agent.evals.runner import build_judge, score_question
from tilik_agent.evals.scores import format_report

QUESTION = (
    "hi, siapa kamu?"
)


def main() -> None:
    print(QUESTION)
    result, scores = score_question(QUESTION, build_judge())
    print(f"\n=== answer ===\n{result.answer}")
    print(f"\n=== scores ===\n{format_report([('question', scores)])}")


if __name__ == "__main__":
    main()
