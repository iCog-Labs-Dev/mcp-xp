# Embedding throughput observations

Notes on how long the `BackgroundIndexer`'s per-cycle embed loop actually
takes against a self-hosted mxbai-embed-large on ollama. Recorded from live hetzner staging runs on
2026-08-05 while chasing the wrong-dim `generic_galaxy_workflow` bug.

## Setup at time of measurement

- Embedder: `mxbai-embed-large:latest` via ollama's OpenAI-compat endpoint
- Client: `OpenAIProvider` at concurrency 5 (`asyncio.Semaphore(5)`),
  batch size 100
- Content truncated to 1200 chars before embedding
- Two batches (116, 117) had one item each that still overflowed context
  at 1200 chars → per-item retry, one item dropped per batch

## Workflows collection — 548 items, 6 batches

| stage | time | delta |
|-------|------|-------|
| `Storing scraped galaxy data` | 11:27:09 | — |
| `Generating vector embeddings` | 11:27:09 | 0s |
| `OpenAI embeddings generated (548 vectors)` | 11:29:11 | 2m 2s |
| collection delete + create + upsert | 11:29:12 | ~1s |

**Total: ~2 minutes for 548 workflows, all six batches happy path.**

## Tools collection — 16,811 items, 169 batches (first run)

Cold start on ollama. Progress logs every 10 batches.

| checkpoint | time | since start | delta from prev | per-10-batches |
|-----------|------|-------------|-----------------|----------------|
| embed start | 11:49:54 | 0m | — | — |
| 10/169 | 11:57:59 | 8m 5s | 8m 5s | 8m 5s |
| 20/169 | 12:05:00 | 15m 6s | 7m 1s | 7m 1s |
| 30/169 | 12:08:24 | 18m 30s | 3m 24s | 3m 24s |
| 40/169 | 12:10:59 | 21m 5s | 2m 35s | 2m 35s |
| 50/169 | 12:14:54 | 25m 0s | 3m 55s | 3m 55s |
| 60/169 | 12:17:14 | 27m 20s | 2m 20s | 2m 20s |
| 110/169 | 12:34:28 | 44m 34s | ~17m 14s over 50 | ~3m 27s |
| batch 116 failed | 12:38:30 | 48m 36s | — | per-item retry |
| batch 117 failed | 12:38:32 | 48m 38s | — | per-item retry |
| 120/169 | 12:39:10 | 49m 16s | 4m 42s | 4m 42s |
| 130/169 | 12:44:11 | 54m 17s | 5m 1s | 5m 1s |
| 140/169 | 12:47:33 | 57m 39s | 3m 22s | 3m 22s |
| 150/169 | 12:52:29 | 62m 35s | 4m 56s | 4m 56s |
| 160/169 | 12:56:09 | 66m 15s | 3m 40s | 3m 40s |
| batch 117 single-item fail (item len=1200) | 12:55:02 | 65m 8s | — | one item dropped |
| batch 116 single-item fail (item len=1200) | 13:04:17 | 74m 23s | — | one item dropped |
| batch 116 per-item retry done (99/100) | 13:04:40 | 74m 46s | — | — |
| batch 117 per-item retry done (99/100) | 13:04:42 | 74m 48s | — | — |
| 169/169 (complete) | 13:04:42 | 74m 48s | — | — |
| `OpenAI embeddings generated (16811 vectors from 169 batches)` | 13:04:42 | 74m 48s | — | — |

**Total: ~1h 15m for 16,811 tools in this run, dropping 2 items that were
too dense for mxbai's context even at 1200 chars.**

Note: this run *rolled back* at the numpy conversion step in
`utils.py::get_embeddings` because `np.array(raw)` couldn't handle
`None` sentinels from the two dropped items. Fixed in `1d4963e`. No
points were written to Qdrant on this run.

## Tools collection — 16,811 items, 169 batches (second run, after fix)

Ollama warm from the first run.  Same two batches (116, 117) hit the same
two 1200-char items and dropped them via per-item retry.

| checkpoint | time | since embed start | delta from prev |
|-----------|------|-------------------|-----------------|
| scrape start | 13:19:49 | (pre-embed) | — |
| embed start | 13:21:26 | 0m | 1m 37s scrape |
| 10/169 | 13:28:18 | 6m 52s | 6m 52s |
| 20/169 | 13:31:22 | 9m 56s | 3m 4s |
| 30/169 | 13:34:44 | 13m 18s | 3m 22s |
| 40/169 | 13:37:19 | 15m 53s | 2m 35s |
| 50/169 | 13:40:45 | 19m 19s | 3m 26s |
| 60/169 | 13:43:05 | 21m 39s | 2m 20s |
| 70/169 | 13:47:16 | 25m 50s | 4m 11s |
| 80/169 | 13:50:43 | 29m 17s | 3m 27s |
| 90/169 | 13:54:46 | 33m 20s | 4m 3s |
| 100/169 | 14:02:12 | 40m 46s | 7m 26s |
| 110/169 | 14:07:15 | 45m 49s | 5m 3s |
| batch 116 failed → retry | 14:12:14 | 50m 48s | — |
| batch 117 failed → retry | 14:12:16 | 50m 50s | — |
| 120/169 | 14:12:53 | 51m 27s | 5m 38s |
| 130/169 | 14:18:08 | 56m 42s | 5m 15s |
| 140/169 | 14:21:20 | 59m 54s | 3m 12s |
| 150/169 | 14:29:35 | 68m 9s | 8m 15s |
| batch 117 single-item fail (len=1200) | 14:37:36 | 76m 10s | — |
| 160/169 | 14:38:41 | 77m 15s | 9m 6s |
| batch 116 single-item fail (len=1200) | 14:41:16 | 79m 50s | — |
| batch 116 per-item retry done (99/100) | 14:41:37 | 80m 11s | — |
| batch 117 per-item retry done (99/100) | 14:41:38 | 80m 12s | — |
| 169/169 (complete) | 14:41:38 | 80m 12s | — |
| dim check + drop 2 Nones + upsert | 14:41:47 | 80m 21s | ~9s to write 16,809 points |
| `content embedded and stored ... succefully` | 14:41:47 | 80m 21s | — |
| `Cycle complete. Sleeping.` | 14:41:47 | 80m 21s | — |

**Total: ~1h 20m for the second run — slightly *slower* than the first
(1h 15m).** The warm-start advantage (first 10 batches were 6m 52s vs
8m 5s) was offset by higher variance in the middle/late batches — the
90→100 delta was 7m 26s, the 140→150 delta was 8m 15s, and 150→160
took 9m 6s.  Two suspected causes:

1. Concurrent user traffic on the same mcp-app pod (real AI-Assistant
   queries were coming in around 12:52-13:04 and again mid-run), sharing
   the ollama connection budget.
2. Batch 117's oversized item was retried at 14:37:36 while other
   batches were still queued behind it — the sequential per-item path
   blocks the semaphore slot for the ~40s the retry loop takes.

## Comparison

| run | start | end | wall | notes |
|-----|-------|-----|------|-------|
| 1 (cold, aborted) | 11:49:54 | 13:04:42 | 74m 48s | rolled back on np.array None-mismatch |
| 2 (warm, success) | 13:21:26 | 14:41:47 | 80m 21s | 16,809 rows written; both timestamps saved |

Interesting that the "warm" run wasn't materially faster than cold —
suggests the concurrency ceiling really is on the ollama-server side
and cache priming on the model itself doesn't help throughput much
once the model is loaded.  What *does* speed up subsequent restarts is
the freshness gate itself: run 3 (any restart within the next 7 days)
will complete in ~1s because both timestamps skip.

## Upsert phase (Qdrant, not ollama)

Once embeddings exist, writing 16,809 vectors to Qdrant took **~7
seconds total** (35 upsert calls of 500 points each, ~0.2s per call).
Bottleneck really is 100% on the embedding side, not storage.

## Trends and observations

- **Cold-start penalty is real.** The first 20 batches took ~15 minutes
  (7m 30s per 10); after warm-up the rate settled at ~3-4 min per 10
  batches — roughly 2× faster.
- **Concurrency ceiling is ollama, not the client.** With
  `Semaphore(5)` the effective throughput is ~2 embeddings/sec sustained.
  Suggests ollama serializes embeddings per model internally regardless
  of batch size.
- **Per-item retry cost is significant when it fires.** A batch that
  falls back to per-item mode takes ~50s (100 sequential single-item
  calls at ~0.5s each) vs. ~2s for a happy batch of 100. On a run with
  many oversized items this is the dominant cost.
- **1200-char truncation is 99% effective.** Only 2 items out of 16,811
  overflowed even at that cap — likely tools with dense XML/JSON
  content packing >0.4 tokens per char. Tightening to 800 chars would
  cover the tail, at the price of losing more context from every item.
- **The tools scrape itself is fast** (~1.5 min to fetch 16,811 tool
  metadata blobs from `usegalaxy.eu`). The bottleneck is the embed, not
  the scrape.

## Implications for the freshness gate

- Full cold cycle: ~1h 20m (workflows + tools).
- Full warm cycle: probably ~50m (mostly tools).
- Skip-both cycle (both timestamps present, both < 7 days old): ~1s.
- The 7-day `LIFESPAN` in `InformerTTLs` is a good default given the
  cold cost — probably don't want to run this more than weekly.
