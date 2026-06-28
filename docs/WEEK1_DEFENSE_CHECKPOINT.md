# Week 1 Project Defense Checkpoint

Complete this checkpoint before Week 1 is considered personally owned.

## Part 1: Architecture Explanation

Without notes, explain:

1. Why the project starts with deterministic backend tools instead of a chatbot.
2. The difference between domain, repository, service, infrastructure, tool, and
   orchestration layers.
3. Why repository interfaces help us move from DuckDB to PostgreSQL.
4. Why Agents SDK tracing cannot replace our audit tables.
5. Why live trading is disabled by default.

## Part 2: Data Flow

Draw this flow:

```text
User request
-> future orchestrator
-> typed tool
-> application service
-> repository interface
-> DuckDB adapter
-> stored result and audit event
-> grounded response
```

Then explain where errors are stored when a tool fails.

## Part 3: Code Walkthrough

Be able to locate and explain:

- `domain/models.py`
- `domain/enums.py`
- `repositories/protocols.py`
- `infrastructure/repositories.py`
- `services/audit_service.py`
- `services/tool_execution_service.py`
- `services/catalog_service.py`
- `tools/catalog_tools.py`
- `config.py`
- `infrastructure/database.py`

## Part 4: Focused Modification

Exercise:

- add a new `ToolCallStatus` value named `CANCELLED`
- add or update a test proving its serialized value is `cancelled`
- run the full test suite

This is intentionally small. The purpose is to prove that you understand the
domain enum and verification workflow.

## Part 5: Debugging Exercise

Temporarily make the success handler in the tool-execution test raise an error.

Observe:

- which test fails
- what status is stored
- which audit events are written

Then restore the test and explain why failure data is persisted before the
exception is re-raised.

## Part 6: Interview Questions

Answer:

1. Why use `Protocol` instead of importing DuckDB repositories everywhere?
2. What does idempotent database initialization mean?
3. What is the difference between an application audit log and an SDK trace?
4. Why should configuration output never print keys?
5. What additional safeguards are needed before live execution?
6. What would move to PostgreSQL in production?
7. How would you test a repository without a real broker?
8. Why record both successful and failed tool calls?

## Sign-Off

Week 1 personal ownership is complete only when:

- architecture explanation completed
- data flow drawn
- code walkthrough completed
- focused modification completed
- debugging exercise completed
- interview questions answered
