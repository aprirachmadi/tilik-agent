# BPS Data Platform

This context describes the statistical information published by BPS and the language used when acquiring and organizing it for later analysis.

## Agent chat terms

**Turn**:
One user message and the assistant reply produced by a single `run_question` call. Each turn is one Langfuse trace.

**Conversation**:
An ordered list of turns in one chat window. Stored in Postgres (`conversation`, `message`) and grouped in Langfuse by `session_id` (the conversation id).

**Direct route**:
The planner returned no lookup steps because the latest message can be answered without new BPS SQL or SP2020 retrieval (for example a greeting or confirming numbers already in the conversation).

**Plan failed route**:
The planner could not produce a valid work plan after retries. The answer writer explains the failure.

## Language

**Dataset**:
A versioned BPS table identified by its source table ID, backend, domain, and applicable period selection.
_Avoid_: File, sheet, report

**Measure**:
The quantity represented by an observation, including its original BPS label, definition, and unit.
_Avoid_: Value type, metric column

**Observation**:
One published value for a measure at a specific period and combination of dimension members.
_Avoid_: Row, record value, cell

**Dimension**:
An axis that qualifies observations, such as geography, sex, age group, employment status, or area classification.
_Avoid_: Column, grouping field

**Dimension member**:
One source-defined value on a dimension, such as Aceh, Perempuan, or Perkotaan.
_Avoid_: Category value, row label

**Geography**:
A period-sensitive administrative or statistical area identified by a BPS code, name, level, and validity interval.
_Avoid_: Location string, province column

**Source unit**:
The unit and scale in which BPS publishes a measure, preserved without conversion.
_Avoid_: Display format

**Canonical unit**:
A normalized unit used for comparison only when the meaning and scale conversion are certain.
_Avoid_: Clean unit

**Status flag**:
A BPS marker qualifying an observation, including missing, unavailable, estimated, revised, provisional, or negligible values.
_Avoid_: Error value

**Provenance**:
The source table, API request context, retrieval time, raw payload identity, and parser version from which an observation was produced.
_Avoid_: Source URL

