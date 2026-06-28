# ADR-007: Verifiable DuckDB Backup And Offline Restore

## Status

Accepted

## Decision

Create portable backups with DuckDB's consistent `EXPORT DATABASE` operation.
Each archive contains:

- the exported schema and Parquet table files
- a versioned JSON manifest
- a SHA-256 checksum and byte size for every exported file
- source table row counts
- the DuckDB version and backup provenance

Verification checks the inventory and checksums, imports the archive into a
temporary database, and compares every restored table count with the manifest.
Restore always targets a new path and refuses to replace the active or an
existing database.

## Why

A filesystem copy taken while the API is writing may not be a trustworthy
backup. A ZIP that has never been restored is also only an assumption.
Consistent export plus an actual temporary import proves that the schema and
table data can be reconstructed.

## Operational Procedure

1. Create a backup through the approver API or `backup-create`.
2. Run `backup-verify` as a scheduled restore drill.
3. Restore to a new path with `backup-restore`.
4. Stop the application, retain the old database, and deliberately swap the
   configured path only after verification.

## Boundaries

The backup covers the DuckDB database. Generated reports and raw source files
must also live on durable storage and follow their own retention policy. A
managed multi-node deployment should use database-native point-in-time recovery
and object-storage versioning.
