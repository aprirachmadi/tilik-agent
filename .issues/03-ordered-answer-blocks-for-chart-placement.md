# Place the chart inside the answer with ordered blocks

Do after 01, because both change `AnswerDraft` and `write_answer()`. 01 already forbids ASCII charts in the answer text.

## Problem

The backend returns the answer as one Markdown string and the chart as a separate object, so the frontend cannot tell where the chart belongs. In [the saved conversation](http://localhost:5173/c/60bd1622-e2cc-47ca-9613-689ec52437dc), the answer opens with "Berikut grafik batang …", but the chart renders below the whole answer.

The frontend's `answerWithoutDuplicateChart()` hides an ASCII chart that matches the rendered one. That guesses from the shape of the text; it is not a content contract.

## Current behavior

- `write_answer` runs before `build_chart` (`src/tilik_agent/agent/graph.py`), and `chart_needed()` decides afterwards whether a chart is built.
- `QuestionResult`, `QuestionResponse`, `ConversationMessage` and the `message` table carry `answer`/`content` and `chart` as separate fields. `MessageList.tsx` always renders the chart after the text.

## Decision

- The writer marks the chart position with a placeholder line, and code turns the text into ordered blocks after `build_chart`. The graph order stays the same, and chart values stay validated by `build_chart()`.
- There is only one chart per answer, so a chart block refers to the message's `chart` field. It has no id and no copy of the data.

## Proposed work

1. **Placeholder**:
   - In `_answer_prompt()`, tell the writer to put `[[chart]]` on its own line where a chart would help the explanation, for example after a one-sentence intro.
   - The writer cannot know whether `chart_needed()` will build a chart, so the marker is a request, not a guarantee.
   - The number check from 01 must ignore the marker.
2. **Blocks** (`answer_blocks(text, chart) -> tuple[str, list[dict] | None]` in `answer.py`, called from `question_result()`):
   - Block types: `{"type": "text", "text": ...}` and `{"type": "chart"}`.
   - When there is a chart:
     - Split the text at the first marker and remove any other markers.
     - With no marker, put the chart after the first paragraph.
     - Drop empty text blocks.
   - When there is no chart (not needed, or `build_chart` failed):
     - Remove all markers.
     - Return no chart block, so there is never an empty chart slot.
   - Return the text with markers removed as `answer`, so saved content, history prompts and older clients never see the marker.
3. **API and storage**:
   - Add `blocks: list[dict] | None` to `QuestionResult`, `QuestionResponse` and `ConversationMessage`.
   - Add a nullable JSON `blocks` column to `Message` with Alembic migration `0004`, and persist it in `chat/storage.py`.
   - Keep `answer`/`content` and `chart` unchanged.
   - Cover `POST /questions`, the stream result and `GET /conversations/{id}`.
4. **Frontend**:
   - Add `blocks` to `frontend/src/api/types.ts`.
   - In `MessageList.tsx`, render blocks in order when present: text with the existing Markdown renderer, and chart with `ChartCard` using `message.chart`.
   - Messages without blocks keep the current text-then-chart layout and `answerWithoutDuplicateChart()`.

## Acceptance criteria

- A fresh age-group chart request returns intro text, then the chart, then interpretation, with no marker in `answer` or in the saved `content`.
- With no marker, the chart follows the first paragraph. With no chart, there is no chart block and no marker in the text.
- The stream result and the reloaded conversation return the same blocks.
- Conversations saved before this change load and render as they do today.
- Tests cover `answer_blocks()` (marker, no marker, extra markers, no chart), the API response and storage round trip, and loading old messages without blocks.

## Relevant code

- `src/tilik_agent/agent/answer.py`: `_answer_prompt()`, `write_answer()`, `build_chart()`
- `src/tilik_agent/agent/runner.py`: `QuestionResult`, `question_result()`
- `src/tilik_agent/api.py`, `src/tilik_agent/chat/models.py`, `src/tilik_agent/chat/storage.py`, `alembic/versions/`
- `frontend/src/api/types.ts`, `frontend/src/components/MessageList.tsx`
