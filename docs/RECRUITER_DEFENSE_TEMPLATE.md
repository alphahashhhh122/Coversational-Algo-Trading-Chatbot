# Recruiter Defense Template

Create one copy of this template for each major module.

## Module

Name:

Resume claim supported:

## Problem And Scope

What problem does it solve?

Who or what calls it?

What does it deliberately not do?

## Architecture

Where does it sit in the system?

```text
Caller -> Layer -> Module -> Dependency -> Storage/External System
```

What are its inputs and outputs?

Which IDs and database tables are involved?

## Technology Choice

What technology or pattern is used?

Why is it appropriate here?

What alternative was considered?

Why was that alternative not selected now?

## Correctness And Safety

What invariants must always hold?

What are the main failure modes?

How are errors stored and exposed?

How are idempotency, reproducibility, and security handled?

## Testing And Evidence

Which automated tests prove it?

Which command verifies it?

Which stored rows or artifacts can be inspected?

Which measured number can be used on the resume?

## Trade-Offs And Production Evolution

What is the current limitation?

What changes with more users, more data, or live trading?

What would be migrated, cached, queued, or monitored?

## Ownership Proof

Focused modification completed:

Failure diagnosed:

Weakest interview answer:

Follow-up improvement:

## Question Drill

1. Why does this module exist?
2. Why is it in this layer?
3. Why did you choose this technology?
4. What happens end to end on success?
5. What happens on failure?
6. What is stored and why?
7. How do you test it?
8. What alternatives did you reject?
9. What breaks at scale?
10. What would you change before production/live use?
