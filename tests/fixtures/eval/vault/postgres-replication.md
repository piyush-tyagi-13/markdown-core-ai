# PostgreSQL Streaming Replication

PostgreSQL replicates data from a primary to one or more standby servers by
shipping the write-ahead log. The primary streams WAL records over a replication
connection as transactions commit. Each standby replays those records to stay in
sync, and can serve read-only queries. Synchronous replication waits for a standby
to confirm the write before the commit returns, trading latency for durability.
Replication slots prevent the primary from discarding WAL a standby still needs.
