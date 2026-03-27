# Decision Memo (March 2026): Context Engine Architecture & Retrieval Strategy

## Why this memo exists
This memo captures the March 2026 architecture decision and turns it into an implementation sequence that fits Context Engine's constraints:

- local-first, privacy preserving
- single-maintainer friendly
- CPU-capable on macOS
- incremental, test-driven evolution

## Decisions

### 1) Keep the current core architecture
Continue with a single-process FastAPI service + MCP stdio + embedded zvec storage. Avoid introducing external hosted/vector infrastructure unless requirements materially change.

### 2) Prioritize ingestion/chunking before model churn
Treat structure-aware chunking and metadata normalization as the first retrieval-quality lever. Focus on:

- heading/section-aware HTML chunking
- code-block-aware chunking for mixed prose+code pages
- timestamp-aware transcript chunking
- canonical URL + content-hash deduplication

### 3) Move from dense-only to optional hybrid retrieval
Retain dense retrieval as baseline, and add a lightweight lexical lane (BM25/inverted index) with score/rank fusion behind a mode flag (`dense`, `hybrid`, `hybrid_rerank`).

### 4) Upgrade embeddings pragmatically
Default upgrade target: `nomic-ai/nomic-embed-text-v1.5` (with configurable output dimension). Keep `BAAI/bge-base-en-v1.5` as a fallback option and leave room for a multilingual path later.

### 5) Add reranking as opt-in precision mode
Use reranking only on a small candidate set and only when explicitly requested (`high_precision=true`) to preserve responsive default search latency.

### 6) Measure every retrieval change
Adopt an in-repo evaluation harness with a golden query set and retrieval metrics (Recall@k, nDCG@k, first-hit rank). Treat benchmark deltas as merge criteria for retrieval/ingestion changes.

### 7) Harden localhost security posture
Require token auth for write operations, keep strict CORS, default to localhost binds, and maintain MCP stdio as the default transport.

## Implementation roadmap

## Phase 1 (Now): high-ROI, low-risk
1. Implement structure-aware chunking and richer chunk metadata.
2. Add canonical URL normalization + duplicate detection during ingestion.
3. Add evaluation harness (`eval/`) with a small golden dataset.
4. Document and enforce baseline security posture (localhost bind, token requirements, CORS allowlist).

## Phase 2 (Next): quality lift
1. Add configurable embedding backend and migrate default to Nomic Embed v1.5.
2. Implement optional lexical retrieval and fusion path.
3. Add multi-collection search normalization and source weighting controls.

## Phase 3 (Later): precision mode + hardening
1. Add optional reranking over top-N candidates.
2. Add freshness policy controls (conditional fetch/recrawl cadence).
3. Add optional encryption-at-rest mode for high-sensitivity local usage.

## Repository execution checklist

- [ ] Add `eval/golden_queries.yaml` with initial 30+ real agent queries.
- [ ] Add `eval/run_eval.py` to compute Recall@5/10/20 and nDCG@5/10/20.
- [ ] Add `search.mode` support (`dense`, `hybrid`, `hybrid_rerank`) in API and MCP tool wrappers.
- [ ] Introduce normalized metadata schema (`source_type`, `content_type`, `collection`, `updated_at`, `language`).
- [ ] Add regression gate in CI for retrieval metric drops beyond tolerance.
- [ ] Publish tuning defaults in docs (chunk size/overlap, candidate k, fusion weights).

## Out of scope (for now)

- External hosted vector DB migration.
- Framework-heavy orchestration rewrites.
- Always-on heavy reranking for all queries.
- Frequent embedding model swaps without benchmark evidence.

## Success criteria

- Improved Recall@10 and nDCG@10 on the repo golden set.
- Lower rate of irrelevant top-5 retrieval for code/API identifier queries.
- No regression to local-first ergonomics (simple setup, local-only default, low operational overhead).
