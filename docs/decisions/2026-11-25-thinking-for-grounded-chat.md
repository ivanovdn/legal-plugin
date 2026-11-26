# Grounded doc chat thinks first; plain questions stay on the fast model

Date: 2026-11-25 · Decided by: the project owner · Status: decided

Related: [roadmap.md](../roadmap.md) — *Choose the chat model and its sampling settings on a scored case set* · [wiki.md](../wiki.md) constraint 1 (temperature 0), which this note narrows

## Context
After the chat prompt audit ([shipped log](../shipped-log.md), 2026-11-25), qwen3.6 still failed plain edit requests on replays of real pilot turns: "I have updated the signatory" with no edit, the wrong clause edited, a refusal to draft a liability cap. Each candidate was replayed on Spark against the same 9 turns and scored by one script, a case passing only if every run passed (the worst run is the one an attorney applies). Traces are in VM Phoenix, project `model-experiments`.

## Options
1. **gpt-oss:120b.** Followed edit requests well, but gave generic advice on a real SOW (it advised making a standalone SOW "subject to the Trinetix MSA"), added cap exceptions the playbook lacks, took 15 s median, and on Spark it pushes compliance-bot's qwen3.6 out of memory on every alternation.
2. **qwen3.6-27b (dense).** Good on the one long case tried, but 243 s for it, about 9 tokens/s.
3. **qwen3.6 with other non-thinking sampling.** Temperature 1 and the model card's non-thinking set (0.7 / 0.8) both did worse than temperature 0.
4. **qwen3.6 thinking on every turn.** Slows "who signs?" for no gain.
5. **qwen3.6 thinking only on grounded turns** (the playbook attached: edits, firm positions, clause judgments), with the card's thinking-mode sampling; plain questions stay non-thinking at temperature 0.
6. **No change.**

## Decision
Option 5, behind `chat_think_grounded` (default on). Grounded turns use `_build_thinking_llm`: thinking on, temperature 1.0, top_p 0.95, top_k 20 (min_p 0 and presence penalty 1.5 from the model's Modelfile), an 8,192-token output cap. Wiki constraint 1 now excepts these turns.

## Why
| qwen3.6 on the 9 cases | Cases right every run | Runs right | Median / slowest |
|---|---|---|---|
| Before: no thinking, temperature 0 | 6/9 | 66% | 7 s / 34 s |
| Card non-thinking set | 3/9 | 59% | 6 s / 26 s |
| Thinking on, card thinking set (direct calls) | 7/9 | 85% | 31 s / 98 s |
| **This change, through the real chat code path** | **7/9** | **81%** | 22 s / 92 s (plain questions 2–6 s) |

- **The deciding fact:** thinking fixed the failures that matter most, the attorney's own edit requests (the signatory change, the signature block) and the standalone-SOW advice, where temperature and sampling changes made things worse.
- **Temperature 1, not 0:** the Qwen3.6 card recommends temperature 1.0 for thinking mode and warns against greedy decoding with it.
- **Grounded turns only:** the attorney already waits longer for these, and plain questions keep their 2–6 s.

## Open questions
- **Cap exceptions** the playbook does not list still appear in some drafted caps on every model and setting tried; that needs a code check, not a setting.
- **Empty answers:** 1 thinking run in 48 returned reasoning and no answer. The turn now asks the fast model once and records `thinking_empty_answer`; watch how often it fires.
- **Ungrounded edit requests** ("expand term for 3 years") never reach the thinking model; one still edits the wrong clause.
- **Nine cases** is a small set. Revisit when the case set grows, or if attorneys find the grounded wait too long.
- **Reviews** already think, but at temperature 0, which the card advises against for thinking mode. They have produced complete reviews so far; not changed here.
