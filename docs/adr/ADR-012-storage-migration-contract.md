# ADR-012: Machine-Checked Storage Migration Contract

## Status

Accepted

## Decision

Generate the multi-store production migration plan from the active governed
schema:

- transactional and operational tables move to PostgreSQL
- `options_ohlcv` moves to partitioned Parquet in object storage
- preserved `legacy_*` tables move to immutable archive storage

Generation introspects columns, defaults, primary and unique keys, row counts,
and selected logical foreign keys. It produces PostgreSQL DDL with verified
relationships and workload indexes plus a table-by-table JSON migration
manifest. Any new unclassified table, unsupported type, or orphaned declared
relationship fails generation and CI.

## Why

A prose statement that PostgreSQL will be added later is not a migration plan.
The source schema evolves continuously, so the target contract must evolve with
it and fail visibly when ownership is missing.

Market candles are intentionally excluded from PostgreSQL because high-volume
append-oriented analytical history belongs in partitioned columnar storage.
Transactional state, identities, approvals, ledgers, audit metadata, and
operational control remain relational.

## Cutover

1. Freeze writes and create a verified recovery backup.
2. Load PostgreSQL tables in dependency order.
3. Export and checksum partitioned market-history Parquet.
4. Verify counts, keys, relationships, and sampled content hashes.
5. Shadow reads against the new stores.
6. Switch transactional writes with a retained rollback window.
