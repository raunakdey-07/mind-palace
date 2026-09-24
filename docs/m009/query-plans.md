# M009 query plans

These are real PostgreSQL `EXPLAIN (ANALYZE, BUFFERS, FORMAT JSON)` results
for the canonical feed query at 10,000 versions. Boundary parameters and
fixture identifiers are redacted; the runner asserts tuple keyset ordering,
absence of `OFFSET`, and use of `idx_memory_versions_observed`.

## first

- keyset predicate: `False`
- no OFFSET: `True`
- index use observed: `True`
- indexes: `idx_memory_versions_observed, memory_documents_pkey`

```json
[
  {
    "Execution Time": 0.519,
    "Plan": {
      "Actual Loops": 1,
      "Actual Rows": 51,
      "Actual Startup Time": 0.069,
      "Actual Total Time": 0.478,
      "Async Capable": false,
      "Local Dirtied Blocks": 0,
      "Local Hit Blocks": 0,
      "Local Read Blocks": 0,
      "Local Written Blocks": 0,
      "Node Type": "Limit",
      "Parallel Aware": false,
      "Plan Rows": 51,
      "Plan Width": 464,
      "Plans": [
        {
          "Actual Loops": 1,
          "Actual Rows": 51,
          "Actual Startup Time": 0.068,
          "Actual Total Time": 0.474,
          "Async Capable": false,
          "Inner Unique": true,
          "Join Type": "Inner",
          "Local Dirtied Blocks": 0,
          "Local Hit Blocks": 0,
          "Local Read Blocks": 0,
          "Local Written Blocks": 0,
          "Node Type": "Nested Loop",
          "Parallel Aware": false,
          "Parent Relationship": "Outer",
          "Plan Rows": 10000,
          "Plan Width": 464,
          "Plans": [
            {
              "Actual Loops": 1,
              "Actual Rows": 51,
              "Actual Startup Time": 0.041,
              "Actual Total Time": 0.071,
              "Alias": "v",
              "Async Capable": false,
              "Index Cond": "(corpus_id = '<redacted-id>'::bpchar)",
              "Index Name": "idx_memory_versions_observed",
              "Local Dirtied Blocks": 0,
              "Local Hit Blocks": 0,
              "Local Read Blocks": 0,
              "Local Written Blocks": 0,
              "Node Type": "Index Scan",
              "Parallel Aware": false,
              "Parent Relationship": "Outer",
              "Plan Rows": 10000,
              "Plan Width": 490,
              "Relation Name": "memory_versions",
              "Rows Removed by Index Recheck": 0,
              "Scan Direction": "Forward",
              "Shared Dirtied Blocks": 0,
              "Shared Hit Blocks": 9,
              "Shared Read Blocks": 0,
              "Shared Written Blocks": 0,
              "Startup Cost": 0.41,
              "Temp Read Blocks": 0,
              "Temp Written Blocks": 0,
              "Total Cost": 3765.38
            },
            {
              "Actual Loops": 51,
              "Actual Rows": 1,
              "Actual Startup Time": 0.007,
              "Actual Total Time": 0.007,
              "Alias": "d",
              "Async Capable": false,
              "Index Cond": "((corpus_id = '<redacted-id>'::bpchar) AND (id = v.memory_document_id))",
              "Index Name": "memory_documents_pkey",
              "Local Dirtied Blocks": 0,
              "Local Hit Blocks": 0,
              "Local Read Blocks": 0,
              "Local Written Blocks": 0,
              "Node Type": "Index Scan",
              "Parallel Aware": false,
              "Parent Relationship": "Inner",
              "Plan Rows": 1,
              "Plan Width": 169,
              "Relation Name": "memory_documents",
              "Rows Removed by Index Recheck": 0,
              "Scan Direction": "Forward",
              "Shared Dirtied Blocks": 0,
              "Shared Hit Blocks": 204,
              "Shared Read Blocks": 0,
              "Shared Written Blocks": 0,
              "Startup Cost": 0.41,
              "Temp Read Blocks": 0,
              "Temp Written Blocks": 0,
              "Total Cost": 0.66
            }
          ],
          "Shared Dirtied Blocks": 0,
          "Shared Hit Blocks": 213,
          "Shared Read Blocks": 0,
          "Shared Written Blocks": 0,
          "Startup Cost": 0.82,
          "Temp Read Blocks": 0,
          "Temp Written Blocks": 0,
          "Total Cost": 10393.13
        }
      ],
      "Shared Dirtied Blocks": 0,
      "Shared Hit Blocks": 213,
      "Shared Read Blocks": 0,
      "Shared Written Blocks": 0,
      "Startup Cost": 0.82,
      "Temp Read Blocks": 0,
      "Temp Written Blocks": 0,
      "Total Cost": 53.82
    },
    "Planning": {
      "Local Dirtied Blocks": 0,
      "Local Hit Blocks": 0,
      "Local Read Blocks": 0,
      "Local Written Blocks": 0,
      "Shared Dirtied Blocks": 0,
      "Shared Hit Blocks": 262,
      "Shared Read Blocks": 0,
      "Shared Written Blocks": 0,
      "Temp Read Blocks": 0,
      "Temp Written Blocks": 0
    },
    "Planning Time": 0.707,
    "Triggers": []
  }
]
```

## middle

- keyset predicate: `True`
- no OFFSET: `True`
- index use observed: `True`
- indexes: `idx_memory_versions_observed, memory_documents_pkey`

```json
[
  {
    "Execution Time": 0.554,
    "Plan": {
      "Actual Loops": 1,
      "Actual Rows": 51,
      "Actual Startup Time": 0.087,
      "Actual Total Time": 0.51,
      "Async Capable": false,
      "Local Dirtied Blocks": 0,
      "Local Hit Blocks": 0,
      "Local Read Blocks": 0,
      "Local Written Blocks": 0,
      "Node Type": "Limit",
      "Parallel Aware": false,
      "Plan Rows": 51,
      "Plan Width": 464,
      "Plans": [
        {
          "Actual Loops": 1,
          "Actual Rows": 51,
          "Actual Startup Time": 0.086,
          "Actual Total Time": 0.506,
          "Async Capable": false,
          "Inner Unique": true,
          "Join Type": "Inner",
          "Local Dirtied Blocks": 0,
          "Local Hit Blocks": 0,
          "Local Read Blocks": 0,
          "Local Written Blocks": 0,
          "Node Type": "Nested Loop",
          "Parallel Aware": false,
          "Parent Relationship": "Outer",
          "Plan Rows": 4483,
          "Plan Width": 464,
          "Plans": [
            {
              "Actual Loops": 1,
              "Actual Rows": 51,
              "Actual Startup Time": 0.042,
              "Actual Total Time": 0.059,
              "Alias": "v",
              "Async Capable": false,
              "Index Cond": "((corpus_id = '<redacted-id>'::bpchar) AND (ROW(observed_at, id) > ROW('<redacted-timestamp>'::timestamp with time zone, '<redacted>'::bpchar)))",
              "Index Name": "idx_memory_versions_observed",
              "Local Dirtied Blocks": 0,
              "Local Hit Blocks": 0,
              "Local Read Blocks": 0,
              "Local Written Blocks": 0,
              "Node Type": "Index Scan",
              "Parallel Aware": false,
              "Parent Relationship": "Outer",
              "Plan Rows": 4483,
              "Plan Width": 490,
              "Relation Name": "memory_versions",
              "Rows Removed by Index Recheck": 0,
              "Scan Direction": "Forward",
              "Shared Dirtied Blocks": 0,
              "Shared Hit Blocks": 9,
              "Shared Read Blocks": 0,
              "Shared Written Blocks": 0,
              "Startup Cost": 0.41,
              "Temp Read Blocks": 0,
              "Temp Written Blocks": 0,
              "Total Cost": 2746.86
            },
            {
              "Actual Loops": 51,
              "Actual Rows": 1,
              "Actual Startup Time": 0.008,
              "Actual Total Time": 0.008,
              "Alias": "d",
              "Async Capable": false,
              "Index Cond": "((corpus_id = '<redacted-id>'::bpchar) AND (id = v.memory_document_id))",
              "Index Name": "memory_documents_pkey",
              "Local Dirtied Blocks": 0,
              "Local Hit Blocks": 0,
              "Local Read Blocks": 0,
              "Local Written Blocks": 0,
              "Node Type": "Index Scan",
              "Parallel Aware": false,
              "Parent Relationship": "Inner",
              "Plan Rows": 1,
              "Plan Width": 169,
              "Relation Name": "memory_documents",
              "Rows Removed by Index Recheck": 0,
              "Scan Direction": "Forward",
              "Shared Dirtied Blocks": 0,
              "Shared Hit Blocks": 204,
              "Shared Read Blocks": 0,
              "Shared Written Blocks": 0,
              "Startup Cost": 0.41,
              "Temp Read Blocks": 0,
              "Temp Written Blocks": 0,
              "Total Cost": 0.95
            }
          ],
          "Shared Dirtied Blocks": 0,
          "Shared Hit Blocks": 213,
          "Shared Read Blocks": 0,
          "Shared Written Blocks": 0,
          "Startup Cost": 0.82,
          "Temp Read Blocks": 0,
          "Temp Written Blocks": 0,
          "Total Cost": 7002.55
        }
      ],
      "Shared Dirtied Blocks": 0,
      "Shared Hit Blocks": 213,
      "Shared Read Blocks": 0,
      "Shared Written Blocks": 0,
      "Startup Cost": 0.82,
      "Temp Read Blocks": 0,
      "Temp Written Blocks": 0,
      "Total Cost": 80.47
    },
    "Planning": {
      "Local Dirtied Blocks": 0,
      "Local Hit Blocks": 0,
      "Local Read Blocks": 0,
      "Local Written Blocks": 0,
      "Shared Dirtied Blocks": 0,
      "Shared Hit Blocks": 262,
      "Shared Read Blocks": 0,
      "Shared Written Blocks": 0,
      "Temp Read Blocks": 0,
      "Temp Written Blocks": 0
    },
    "Planning Time": 0.661,
    "Triggers": []
  }
]
```

## tail

- keyset predicate: `True`
- no OFFSET: `True`
- index use observed: `True`
- indexes: `idx_memory_versions_observed, memory_documents_pkey`

```json
[
  {
    "Execution Time": 0.633,
    "Plan": {
      "Actual Loops": 1,
      "Actual Rows": 50,
      "Actual Startup Time": 0.578,
      "Actual Total Time": 0.584,
      "Async Capable": false,
      "Local Dirtied Blocks": 0,
      "Local Hit Blocks": 0,
      "Local Read Blocks": 0,
      "Local Written Blocks": 0,
      "Node Type": "Limit",
      "Parallel Aware": false,
      "Plan Rows": 45,
      "Plan Width": 464,
      "Plans": [
        {
          "Actual Loops": 1,
          "Actual Rows": 50,
          "Actual Startup Time": 0.577,
          "Actual Total Time": 0.58,
          "Async Capable": false,
          "Local Dirtied Blocks": 0,
          "Local Hit Blocks": 0,
          "Local Read Blocks": 0,
          "Local Written Blocks": 0,
          "Node Type": "Sort",
          "Parallel Aware": false,
          "Parent Relationship": "Outer",
          "Plan Rows": 45,
          "Plan Width": 464,
          "Plans": [
            {
              "Actual Loops": 1,
              "Actual Rows": 50,
              "Actual Startup Time": 0.095,
              "Actual Total Time": 0.53,
              "Async Capable": false,
              "Inner Unique": true,
              "Join Type": "Inner",
              "Local Dirtied Blocks": 0,
              "Local Hit Blocks": 0,
              "Local Read Blocks": 0,
              "Local Written Blocks": 0,
              "Node Type": "Nested Loop",
              "Parallel Aware": false,
              "Parent Relationship": "Outer",
              "Plan Rows": 45,
              "Plan Width": 464,
              "Plans": [
                {
                  "Actual Loops": 1,
                  "Actual Rows": 50,
                  "Actual Startup Time": 0.056,
                  "Actual Total Time": 0.066,
                  "Alias": "v",
                  "Async Capable": false,
                  "Exact Heap Blocks": 4,
                  "Local Dirtied Blocks": 0,
                  "Local Hit Blocks": 0,
                  "Local Read Blocks": 0,
                  "Local Written Blocks": 0,
                  "Lossy Heap Blocks": 0,
                  "Node Type": "Bitmap Heap Scan",
                  "Parallel Aware": false,
                  "Parent Relationship": "Outer",
                  "Plan Rows": 45,
                  "Plan Width": 490,
                  "Plans": [
                    {
                      "Actual Loops": 1,
                      "Actual Rows": 50,
                      "Actual Startup Time": 0.049,
                      "Actual Total Time": 0.049,
                      "Async Capable": false,
                      "Index Cond": "((corpus_id = '<redacted-id>'::bpchar) AND (ROW(observed_at, id) > ROW('<redacted-timestamp>'::timestamp with time zone, '<redacted>'::bpchar)))",
                      "Index Name": "idx_memory_versions_observed",
                      "Local Dirtied Blocks": 0,
                      "Local Hit Blocks": 0,
                      "Local Read Blocks": 0,
                      "Local Written Blocks": 0,
                      "Node Type": "Bitmap Index Scan",
                      "Parallel Aware": false,
                      "Parent Relationship": "Outer",
                      "Plan Rows": 45,
                      "Plan Width": 0,
                      "Shared Dirtied Blocks": 0,
                      "Shared Hit Blocks": 5,
                      "Shared Read Blocks": 0,
                      "Shared Written Blocks": 0,
                      "Startup Cost": 0.0,
                      "Temp Read Blocks": 0,
                      "Temp Written Blocks": 0,
                      "Total Cost": 8.86
                    }
                  ],
                  "Recheck Cond": "((corpus_id = '<redacted-id>'::bpchar) AND (ROW(observed_at, id) > ROW('<redacted-timestamp>'::timestamp with time zone, '<redacted>'::bpchar)))",
                  "Relation Name": "memory_versions",
                  "Rows Removed by Index Recheck": 0,
                  "Shared Dirtied Blocks": 0,
                  "Shared Hit Blocks": 9,
                  "Shared Read Blocks": 0,
                  "Shared Written Blocks": 0,
                  "Startup Cost": 8.87,
                  "Temp Read Blocks": 0,
                  "Temp Written Blocks": 0,
                  "Total Cost": 147.7
                },
                {
                  "Actual Loops": 50,
                  "Actual Rows": 1,
                  "Actual Startup Time": 0.009,
                  "Actual Total Time": 0.009,
                  "Alias": "d",
                  "Async Capable": false,
                  "Index Cond": "((corpus_id = '<redacted-id>'::bpchar) AND (id = v.memory_document_id))",
                  "Index Name": "memory_documents_pkey",
                  "Local Dirtied Blocks": 0,
                  "Local Hit Blocks": 0,
                  "Local Read Blocks": 0,
                  "Local Written Blocks": 0,
                  "Node Type": "Index Scan",
                  "Parallel Aware": false,
                  "Parent Relationship": "Inner",
                  "Plan Rows": 1,
                  "Plan Width": 169,
                  "Relation Name": "memory_documents",
                  "Rows Removed by Index Recheck": 0,
                  "Scan Direction": "Forward",
                  "Shared Dirtied Blocks": 0,
                  "Shared Hit Blocks": 200,
                  "Shared Read Blocks": 0,
                  "Shared Written Blocks": 0,
                  "Startup Cost": 0.41,
                  "Temp Read Blocks": 0,
                  "Temp Written Blocks": 0,
                  "Total Cost": 7.9
                }
              ],
              "Shared Dirtied Blocks": 0,
              "Shared Hit Blocks": 209,
              "Shared Read Blocks": 0,
              "Shared Written Blocks": 0,
              "Startup Cost": 9.28,
              "Temp Read Blocks": 0,
              "Temp Written Blocks": 0,
              "Total Cost": 503.05
            }
          ],
          "Shared Dirtied Blocks": 0,
          "Shared Hit Blocks": 215,
          "Shared Read Blocks": 0,
          "Shared Written Blocks": 0,
          "Sort Key": [
            "v.observed_at",
            "v.id"
          ],
          "Sort Method": "quicksort",
          "Sort Space Type": "Memory",
          "Sort Space Used": 38,
          "Startup Cost": 504.28,
          "Temp Read Blocks": 0,
          "Temp Written Blocks": 0,
          "Total Cost": 504.4
        }
      ],
      "Shared Dirtied Blocks": 0,
      "Shared Hit Blocks": 215,
      "Shared Read Blocks": 0,
      "Shared Written Blocks": 0,
      "Startup Cost": 504.28,
      "Temp Read Blocks": 0,
      "Temp Written Blocks": 0,
      "Total Cost": 504.4
    },
    "Planning": {
      "Local Dirtied Blocks": 0,
      "Local Hit Blocks": 0,
      "Local Read Blocks": 0,
      "Local Written Blocks": 0,
      "Shared Dirtied Blocks": 0,
      "Shared Hit Blocks": 262,
      "Shared Read Blocks": 0,
      "Shared Written Blocks": 0,
      "Temp Read Blocks": 0,
      "Temp Written Blocks": 0
    },
    "Planning Time": 0.671,
    "Triggers": []
  }
]
```
