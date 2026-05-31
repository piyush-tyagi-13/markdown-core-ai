# Python asyncio and Coroutines

The asyncio event loop runs a single thread and schedules coroutines cooperatively.
A coroutine defined with async def suspends at every await, handing control back to
the loop so other tasks can run. await on an I/O operation lets the loop service
thousands of concurrent connections without blocking. asyncio.gather runs awaitables
concurrently and collects their results. CPU-bound work still blocks the loop, so it
belongs in a thread or process pool, not directly inside a coroutine.
