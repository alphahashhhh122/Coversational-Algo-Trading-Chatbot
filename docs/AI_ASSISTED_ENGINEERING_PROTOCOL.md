# AI-Assisted Engineering Protocol

## Principle

AI may generate substantial portions of code. Technical ownership comes from
requirements, review, verification, design judgment, debugging, and explanation,
not from manually typing every character.

## Before Code Generation

For each task, define:

- user or system requirement
- inputs and outputs
- affected architecture layer
- database/storage impact
- safety implications
- acceptance criteria
- required tests
- interview learning objective

## During Generation

Keep changes scoped:

- one module or coherent feature at a time
- explicit file ownership
- no unrelated refactors
- no hidden live-trading behavior
- no secrets in source code

## Mandatory Review

After generation, inspect:

- every changed file
- public functions and classes
- validation logic
- database reads and writes
- state transitions
- exception handling
- logs and audit records
- test coverage
- security and credential handling

## Understanding Test

You should be able to answer:

1. What problem does this module solve?
2. Why is it in this architecture layer?
3. What data enters and leaves?
4. Where is state stored?
5. What can fail?
6. How is it tested?
7. What would change at larger scale?
8. What alternatives did we reject?

## Modification Test

You do not need to rewrite the module from scratch. You must be able to make a
focused change such as:

- add a validation rule
- add a field to a domain model and database schema
- change a risk threshold
- add an order state
- add an API filter
- improve an error path
- add a test case

## Debugging Test

For each major module, practice one failure:

- malformed data
- missing table
- duplicate request
- invalid transition
- unavailable OpenAlgo service
- failed tool call
- unsupported user request

Explain the symptom, root cause, fix, and prevention.

## AI Disclosure In Interviews

Do not volunteer a misleading claim such as "I wrote every line manually."

A strong, honest answer is:

> I used AI-assisted development for parts of the implementation, while I owned
> the architecture, requirements, code review, testing, integration, debugging,
> and technical decisions. I can walk through or modify the critical paths.

The credibility comes from the depth of the walkthrough that follows.

