# Model notes

Checked on 2026-10-16. This is the shortlist of models for Crew agents, what each one costs, and a first guess at which agent gets which model. We will come back to it when we build the real crew for your application.

**Two machines:** you will create the crew on a Free-plan machine first, then move to a machine with a paid plan. Each agent therefore gets two choices: `model` (free, used now) and `paid_model` (the paid pick from section 4, used once `"plan": "paid"` is set in `crewctl.json`). Creating the crew costs nothing on either plan. The risk is *using* a paid model on Free, which can fail on the first prompt, so keep Free-machine agents on free models.

## 1. The models

Credits are multipliers. **Auto = 1.0.** A task that costs 10 credits on Auto costs 20 on Opus 5.5 and 0.5 on Qwen3 Coder Next.

The first ten rows are the list that was supplied, with its numbers. The last four are extra models that also appear in Kiro's docs or on the machine I tested on.

| Model id (use this in `model`) | Credits | Above 272K tokens | Context | Plan | Id spelling |
|---|---|---|---|---|---|
| `claude-opus-5.5` | 2.0x | same | 1M | paid | inferred, verify |
| `claude-sonnet-5.5` | 1.3x | same | 1M | paid | inferred, verify |
| `gpt-5.6-sol` | 4.4x | 8.8x | 1M | paid | confirmed in Crew's code |
| `gpt-5.6-terra` | 2.2x | 4.4x | 1M | paid | confirmed in Crew's code |
| `gpt-5.6-luna` | 0.6x | 1.2x | 1M | paid | confirmed in Crew's code |
| `claude-haiku-4.5` | 0.4x (supplied) | same | 200K | any | confirmed in a Crew model list |
| `deepseek-3.2` | 0.25x | same | 164K | any | confirmed in a Crew model list |
| `minimax-m2.5` | 0.25x | same | 196K | any | confirmed in a Crew model list |
| `glm-5` | 0.5x | same | 200K | any | confirmed in a Crew model list |
| `qwen3-coder-next` | 0.05x | same | 256K | any | confirmed in a Crew model list |
| `auto` | 1.0x | same | 1M | any | confirmed |
| `claude-sonnet-4.5` | not stated | same | 200K | any | confirmed in a Crew model list |
| `minimax-m2.1` | 0.15x | same | 196K | any | confirmed in a Crew model list |
| `claude-sonnet-4` | not stated | same | 200K | ended 2026-10-14 | do not use |

What the columns mean:

- **Above 272K tokens:** the three GPT-5.6 models bill double once a request goes over 272K tokens. The supplied list says Terra and Luna follow Sol's rule; Kiro's docs agree. Keep their context small.
- **Plan:** "paid" means Kiro's docs list the model for Pro and above only. "any" means it is usable on the Free plan too (open-weight models, and Sonnet 4.5). The docs do not say whether Haiku 4.5 is on the Free plan.
- **Id spelling:** a model id has to be spelled exactly right. "confirmed in a Crew model list" means it is in `provider_models.json` on the machine I tested. "confirmed in Crew's code" means the id appears in Crew's source. The two Claude 5.5 ids follow the pattern of the others (`claude-haiku-4.5`) but I found no proof.

**First thing to do on the paid machine:** run `python3 -m crewctl models`. The "IN YOUR CREW LIST" column shows which ids that machine's Crew actually offers. If `claude-opus-5.5` or `claude-sonnet-5.5` is spelled differently there, fix the id in `crewctl/models.json`, or set `"allow_unknown_models": true` in `crewctl.json`.

## 2. Plans

| Plan | Credits per month | Models |
|---|---|---|
| Free | 50 | open-weight models, Sonnet 4.5 |
| Pro ($20) | 1,000 | adds the premium models, including Opus and Sonnet 5.x |
| Pro+ ($40) | 2,000 | same |
| Pro Max ($100) | 5,000 | same |
| Power ($200) | 10,000 | same |

I don't know which plan the target machine has, so the tables below use 1,000 credits (Pro) as the yardstick. Scale them to your plan.

crewctl's `plan` setting in `crewctl.json` is `"free"` or `"paid"`. On `"free"` it rejects models that need a paid plan (Kiro's docs warn that pinning one can make the first prompt fail) and ignores each agent's `paid_model`. On `"paid"` it uses `paid_model` where an agent has one.

On either plan, `crewctl validate` also compares each model with that machine's own Crew model list and warns if it is missing. On a paid machine that is your check that an id is spelled right and available to you.

## 3. How far the credits go

One "unit" below is a task that costs 1 credit on Auto. Divide your credits by the multiplier.

| Model | Units per 1,000 credits (Pro) | Same, above 272K | Units per 50 credits (Free) |
|---|---|---|---|
| `qwen3-coder-next` | 20,000 | | 1,000 |
| `minimax-m2.1` | 6,667 | | 333 |
| `deepseek-3.2` | 4,000 | | 200 |
| `minimax-m2.5` | 4,000 | | 200 |
| `claude-haiku-4.5` | 2,500 | | 125 |
| `glm-5` | 2,000 | | 100 |
| `gpt-5.6-luna` | 1,667 | 833 | 83 |
| `auto` | 1,000 | | 50 |
| `claude-sonnet-5.5` | 769 | | 38 |
| `claude-opus-5.5` | 500 | | 25 |
| `gpt-5.6-terra` | 454 | 227 | 22 |
| `gpt-5.6-sol` | 227 | 113 | 11 |

Real tasks are not all 1 credit. A long investigation with many tool calls costs many units. I have not measured what one of your tasks uses, so treat the table as a way to compare models, not to predict a bill.

### What mixing models saves

An illustration, with **made-up shares of the work** (replace them after you measure): the conductor does 10% of the work, the specialist agents that read logs, traces, metrics and code do 60%, code review does 15%, and the verifier does 15%.

| Crew | Cost per unit of work | Compared with all-Opus |
|---|---|---|
| Everything on `claude-opus-5.5` | 2.0x | 100% |
| Everything on `claude-sonnet-5.5` | 1.3x | 65% |
| Mixed: Opus conductor, `deepseek-3.2` specialists, Sonnet 5.5 review, `gpt-5.6-terra` verifier | 0.88x | 44% |
| Same, with `qwen3-coder-next` specialists | 0.76x | 38% |
| Same as the first mix, with `gpt-5.6-luna` as verifier | 0.64x | 32% |

The point: most of the work is reading, and reading can use a cheap model. The expensive models go where judgment decides the answer.

## 4. First guess: which model for which job

This is a starting point, based on what each model class is generally good at. **I have not tested any of these on your workloads.** The plan is to start with this mix, measure with the eval we designed earlier, and move an agent up or down when its score says so.

| Job | Start with | Cheaper fallback | Stronger option | Why |
|---|---|---|---|---|
| Conductor (splits the work, combines the answers, writes the final report) | `claude-opus-5.5` | `claude-sonnet-5.5` | | Judgment matters most here, and it runs once per task, so the extra cost is small overall. |
| Log, metric and trace specialists (read a lot, extract facts) | `deepseek-3.2` | `qwen3-coder-next` | `claude-haiku-4.5` | High volume and mostly mechanical. Do counting and statistics in code where you can, and let the model write the summary. |
| Code and diff reviewer (finds the risky change) | `claude-sonnet-5.5` | `deepseek-3.2` | `claude-opus-5.5` | Reading a diff well is a judgment task. Compare the cheaper model against it on your eval. |
| Independent verifier (tries to disprove the top finding) | `gpt-5.6-terra` | `gpt-5.6-luna` or `glm-5` | `gpt-5.6-sol` | Use a different model family from the one that made the finding. That cuts the chance that both make the same mistake. |
| Knowledge-base lookup and note writing | `claude-haiku-4.5` | `qwen3-coder-next` | | Short, structured outputs. |
| One rare, very hard question | `gpt-5.6-sol` | `claude-opus-5.5` | | Keep for the exceptions. At 4.4x it is the most expensive model on the list. |

**What to put in `model` (free) while you are on the Free machine:** `claude-sonnet-4.5` for the conductor and the code reviewer, `deepseek-3.2` or `qwen3-coder-next` for the specialists, and `glm-5` or `minimax-m2.5` for the verifier. Put the "Start with" column in `paid_model`. The shipped examples already do this.

Rules of thumb:

1. **Cheapest model that passes the eval wins.** Put the 0.05x to 0.25x models on anything that runs a lot, then raise a model only where scores show a real gap.
2. **Spend on the few agents that decide the answer,** not the ones that fetch and summarize.
3. **Keep GPT-5.6 requests under 272K tokens.** Over that they cost double.
4. **Verify with a different family.** A verifier that shares the author's model shares its blind spots.
5. **Use reasoning effort sparingly.** Set `reasoning_effort` only on agents that need it. Only reasoning-capable models use it; on others the setting is ignored.

The same advice appears in other multi-agent guides: start with one model, then move reading-heavy workers to a cheaper one.

## 5. How to set a model

In an agent file (`crew/<name>.agent.json`):

```json
{ "name": "log-analyst", "model": "qwen3-coder-next", "paid_model": "deepseek-3.2", "tools": ["fs_read"], "prompt": "..." }
```

- Set `model` to the free choice and `paid_model` to the paid one. Which is used depends on `"plan"` in `crewctl.json`.
- `crewctl models` prints this catalog, and shows which ids this machine's Crew has.
- `crewctl validate` checks the id and the plan.
- The model is written into the Kiro agent file only. The crew record's own `model` stays empty, and Crew then uses the agent file's model. If you set a model on the crew record in the dashboard, it overrides the file; `crewctl plan` warns when that happens.
- Leave `model` empty, or use `auto`, to let Kiro choose. According to Kiro's docs, Auto gives Opus-class quality or better on paid plans.

## 6. To check on the paid machine

- Confirm the ids `claude-opus-5.5` and `claude-sonnet-5.5` with `crewctl models`, and in the dashboard model picker.
- Haiku 4.5: the credit number (0.4x) is yours; Kiro's models page does not state it.
- Sonnet 4.5: the credit multiplier is not in the docs I could reach.
- GPT-5.6 models are marked experimental and "rolling out". Your plan may not have them yet.
- Measure real credits per task for each agent after the first runs, and replace section 3 with real numbers.

## Sources

- [Kiro available models](https://kiro.dev/docs/models/available-models/) (credits, contexts, the 272K rule, status)
- [Kiro pricing](https://kiro.dev/pricing/) (plans and credits)
- [Claude Opus 5.5 in Kiro](https://kiro.dev/blog/opus-5-5/)
- Crew's installed `model_registry.py` (confirms the `gpt-5.6-*` ids) and `provider_models.json` (the model list Crew offered on the machine I tested).
