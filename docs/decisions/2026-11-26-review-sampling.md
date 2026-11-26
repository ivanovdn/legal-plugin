# Contract reviews use the card's precise thinking sampling

Date: 2026-11-26 · Decided by: the project owner · Status: decided

Related: [2026-11-25-thinking-for-grounded-chat.md](2026-11-25-thinking-for-grounded-chat.md) (the chat side, whose open question this answers) · [wiki.md](../wiki.md) constraint 1 (temperature 0), which this note narrows · [roadmap.md](../roadmap.md) — *Review rating rules and a code-side list of blank fields*, *Playbook gaps the review tests found*, *Chat "fixes" Trinetix's own template wording*

## Context
Reviews think, but at temperature 0, which the Qwen3.6 card warns against in thinking mode; the chat decision left this open. Each candidate reviewed the three Trinetix templates kept in the dev machine's gitignored `data/` (Model NDA 2025, the BAA template, Model MSA 2025 filled in for a test client) through the real review path (`contract_review` → `llm_caller`), only `think` and the sampling options swapped, on Spark. Traces are in VM Phoenix, project `model-experiments`, experiments "2026-11-26 review sampling test" and "… confirmation".

A run passed only if it: finished within the production output cap; had all seven sections and a "do not send" verdict; named Trinetix's role; raised every blank field as its own Missing Context or Red finding; cited no playbook row id that does not exist; and, on the NDA and MSA (the firm's own templates), put no Red on a template clause.

## Options
| Setting | Runs passing (cap 8,192) | What failed |
|---|---|---|
| Thinking, temperature 0 (before) | 8/12 | a Red on the MSA template's own cap exceptions in every MSA run |
| **Card "thinking, precise tasks": temperature 0.6, top_p 0.95, top_k 20, min_p 0, presence penalty 0** | **17/21** | one BAA review used 10,691 tokens, past the 8,192 cap; 3 of 7 MSA runs had a false Red |
| Card "thinking, general tasks": temperature 1 (what grounded chat uses) | 3/9 | missed BAA blank fields in all 3 runs; 2 of 3 MSA runs over the cap |
| No thinking, temperature 0 | 2/6 | 3 false Reds on the NDA template and a cited row (CONF-004) that does not exist |
| Card non-thinking: temperature 0.7, top_p 0.8 | 1/9 | missed the BAA signature blocks in all 3 runs |

Medians: about 80 s for every thinking setting, 40–45 s without thinking.

## Decision
1. **Reviews (task type `contract_review` only) sample with the precise set**, from `review_temperature`, `review_top_p`, `review_top_k`, `review_min_p`, `review_presence_penalty`. They are sent as raw Ollama options, so nothing comes from the Modelfile. The other skills that reach `llm_caller` stay at temperature 0; they were not measured.
2. **`ollama_num_predict_review` rises from 8,192 to 16,384.** Thinking counts against it, and a cut review would have no findings at all.

## Why
- **Precise over temperature 0:** more runs right at the same median time, and more thorough: it raised the BAA's third-party-beneficiary clause in every run, against none at temperature 0, and listed each blank as its own finding, as the output format asks.
- **With the 16,384 cap**, the precise set passed 20 of 24 runs, a real client SOW from the pilot (not in this repo, reviewed without an MSA as on the VM) included: NDA 7/7, BAA 7/7, MSA 4/7, client SOW 2/3. No blank field went unraised in any of the 24.
- **Temperature 1 suits chat but not reviews:** on the BAA it rated blank fields Yellow or missed them, in every run.

## Open questions
- **False Reds every setting shares**, counted over 42 thinking reviews: BAA indemnity or breach costs called "uncapped" in 10 of 14 runs although the BAA's own cap covers indemnity; the MSA's repository delivery clause rated Red as "IP before payment" although the MSA's IP clause waits for full payment; the BAA's subcontractor-consent clause rated Red in 11 of 14 runs by assuming a delivery model; filled MSA signature blocks raised as blockers for "lacking dates" in 12 of 14. Two rating rules and a definition of an incomplete signature block cut Reds per review from 1.88 to 1.29 in a measured trial, but two blank fields went unraised in 24 runs, so they wait for a code-side list of blank fields (roadmap row *Review rating rules and a code-side list of blank fields*).
- **No review knew a template was Trinetix paper** (0 of 42 thinking runs), so template positions draw Reds. Telling the model which clauses are template wording is the roadmap row *Chat "fixes" Trinetix's own template wording*, now for reviews as well.
- **Gaps between the playbook and the templates**, for the legal team (roadmap row *Playbook gaps the review tests found*).
- **Reviews of a SOW do not get the firm's MSA positions**, so a SOW review cannot see a non-standard governing law: none of the client-SOW runs flagged one.
- **Three templates** plus one client SOW is a small set. Temperature 0 was not repeatable either: its NDA and MSA reviews changed between runs.
