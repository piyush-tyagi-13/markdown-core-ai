# Redis Caching and Eviction

Redis keeps data in memory, making it a fast cache in front of a slower database.
When memory fills up, the maxmemory policy decides what to evict. allkeys-lru drops
the least recently used keys, while volatile-ttl evicts keys closest to expiry. Set
a TTL on cached entries so stale data expires automatically. The cache-aside pattern
reads from Redis first and falls back to the database on a miss, then writes the
result back into the cache for next time.
