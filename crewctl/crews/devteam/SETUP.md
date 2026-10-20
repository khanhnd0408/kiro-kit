# Set up the team on the new machine, step by step

Run every command in a terminal on the **new** machine. Each step says what you should see. If you see something else, go to "If something goes wrong" at the end. Do not skip a step.

## Before you start

You need on the new machine:
- Kiro Crew installed, and you are signed in to your paid Kiro plan.
- Python 3 (`python3 --version` prints a version).
- Git.

## Step 1. Get the files onto the machine

The files must be on the new machine. Either:
- **Git:** `git clone <your-repo-url> kiro-kit` (the repository must contain the `crewctl/crews/devteam` folder; if it is not pushed yet, push it from the old machine first), or
- **Copy:** copy the whole `crewctl` folder by USB or `scp`.

Then go into the folder:
```bash
cd kiro-kit/crewctl
```
You should see the folders `crewctl`, `crews`, `docs`, `examples`, `tests`.

Do not copy a `.crewctl` folder from the old machine. It is that machine's notes and must start empty here.

## Step 2. Make sure Crew has started once

```bash
kirocrew status
```
You should see: `Kiro Crew gateway is running`. If it is not running, open the Crew app (or run `kirocrew gateway`, as your install documents), wait a minute, and try again.

Then ask crewctl whether it can find Crew's own tool servers (`kirocrew-core` and `kirocrew-cron`). crewctl needs them to give the orchestrator its spawn tools:
```bash
python3 -m crewctl crew-servers
```
You should see `found` next to both and the line `Nothing to fix.`

If it says `missing`, follow what it prints. In short:
1. Start Crew (the desktop app, or the gateway), wait a minute, and run the command again. Crew writes `~/.kiro/agents/kirocrew.json` when its gateway starts.
2. Run `kirocrew doctor`. A known cause: when Crew cannot find its own `kirocrew` launcher (for example, the gateway runs as a background service whose PATH does not include it), it leaves the entry out. Put `kirocrew` on that service's PATH and restart the gateway.
3. If the file still does not appear, tell crewctl the command yourself. The command prints a ready-made block. Paste it into `crews/devteam/crewctl.json`, next to `"plan"`. It looks like this (use the real path from `which kirocrew`):
```json
"crew_server_commands": {
  "core": { "command": "/full/path/to/kirocrew", "args": ["mcp-core"] },
  "cron": { "command": "/full/path/to/kirocrew", "args": ["mcp-cron"] }
}
```
Run `python3 -m crewctl crew-servers` again; it should now say `found from crewctl.json`.

### Or set the team up without Crew's tool servers

If you cannot get them working, you can still create the team. In `crews/devteam/crewctl.json` add:
```json
"use_crew_servers": false
```
crewctl then leaves `kirocrew-core` and every `@kirocrew-core/...` tool name out of all ten agent files, and `crew-servers` is no longer needed. `validate` and `plan` will show a warning for each crewmate that loses tools.

What you lose:
- **`orchestrator-agent` cannot start or watch other crewmates**, so the check loop does not exist. It becomes an adviser: its prompt tells it to say so and to give you a plan of which crewmate to open, with the brief to paste. You then open each specialist yourself.
- **The nine specialists lose `skill_search`.** Their skills are still listed as plain skill files in each agent file, which Kiro may load by itself. I have not tested that, so check by asking a specialist which skills it has.
Switch it back to `true` (or delete the line) and run `plan` then `apply` when the servers work.

If `kirocrew` itself is not installed (`kirocrew --version` fails), install Kiro Crew first; nothing else here works without it.

## Step 3. Switch to the paid plan

Open `crews/devteam/crewctl.json` and change one word:
```json
{ "plan": "paid", "kirocrew_bin": "kirocrew", "allow_unknown_models": false }
```
This makes every crewmate use its paid model (Sonnet 5.5 for most, Opus 5.5 for the architect, Go and Rust).

## Step 4. Check the model names exist on this machine

```bash
python3 -m crewctl -c crews/devteam models
```
Look at the column **IN YOUR CREW LIST**. These names must say yes: `claude-sonnet-5.5`, `claude-opus-5.5`, `claude-sonnet-4.5`, `deepseek-3.2`.

If a name is missing, Crew spells it differently on this machine. Find the real spelling in the Crew dashboard's model picker, then replace it in the files `crews/devteam/*.agent.json` (the `paid_model` line). Run this step again until the names match. The names I used for the Claude 5.5 models were not confirmed on a real paid machine, so this check matters.

## Step 5. Check the config

```bash
python3 -m crewctl -c crews/devteam validate
```
You should see ten crewmate lines and the last line: `OK: 10 crewmate(s) valid.`
If you see `ERROR`, read the message; it names the file and the problem.

## Step 6. Install the skills

First look at what will be written (nothing is written yet):
```bash
python3 -m crewctl -c crews/devteam skills plan
```
You should see ten lines starting with `+ create`. Then write them:
```bash
python3 -m crewctl -c crews/devteam skills apply
```
Type `yes` when asked. You should see `Apply complete: 10 created`.

They are written to `~/.kiro/crew/skills/`. Crew re-reads that folder by itself within about 1 to 2 minutes. To confirm, open the Crew dashboard, go to the Skills tab and look for `spec-driven-workflow`.

## Step 7. Create the agent files

```bash
python3 -m crewctl -c crews/devteam plan
```
You should see ten lines starting with `+`. Then:
```bash
python3 -m crewctl -c crews/devteam apply
```
Type `yes`. You should see `Apply complete: 10 created`. The files are in `~/.kiro/agents/`.

## Step 8. Register the crewmates with Crew

An agent file alone is not a crewmate. This step creates the Crew record. First look at the commands:
```bash
python3 -m crewctl -c crews/devteam register
```
You should see ten lines like `kirocrew agent create --name go-agent --kiro-agent go-agent --workspace default`. Then run them:
```bash
python3 -m crewctl -c crews/devteam register --run
```
Check:
```bash
kirocrew agent list
```
All ten names should be in the list.

## Step 9. Check that the descriptions are filled in

Step 8 already set each crewmate's display name, description and triggers (crewctl runs `kirocrew config set agents.<name>.description ...` for you). Crew picks a crewmate by its description and triggers, so confirm them:
```bash
kirocrew config get agents.orchestrator-agent
```
You should see `display_name`, `description` and `triggers` filled in. Then open the dashboard (Agent Capabilities, then Agents) and check that no crewmate says "No description".

If one is empty, run `python3 -m crewctl -c crews/devteam register` again. It lists what is still missing; `register --run` fixes it.

**About the "Capabilities" and template-inheritance fields.** The Capabilities page shows each crewmate's tools, skills, MCP servers, prompt and model, read from its agent file. A crewmate made this way points straight at its own template, which Crew calls "shared" mode, so nothing is inherited from a parent and the "forked from" field stays empty. Inheritance only exists once you open a crewmate's Capabilities page and enroll it, which makes Crew keep a private copy that inherits from the template. Skills appear on that page only after Step 6 has installed them. Enrolling is optional and has a side effect: see `docs/how-crewmates-work.md`, section "Capabilities and inheritance".

## Step 10. Inspector settings (only if you use `inspector-agent`)

The inspector reads your logs, metrics and traces. Give it the addresses of your systems, in the environment that Crew runs in:
```bash
export VICTORIALOGS_URL=http://your-victorialogs:9428
export VICTORIAMETRICS_URL=http://your-victoriametrics:8428
export TEMPO_URL=http://your-tempo:3200
```
If they need a token, also set `VICTORIALOGS_TOKEN`, `VICTORIAMETRICS_TOKEN`, `TEMPO_TOKEN`. Never write a token into a file in this folder. Restart the Crew gateway after setting them so it sees the new values.

Test the collector by itself before trusting the agent (it only reads):
```bash
python3 ~/.kiro/crew/skills/incident-investigation/scripts/collect_evidence.py logs --query '*' --start=-10m
```
You should see a line starting with `# logs:`. Exit code 2 and a message about a URL means the variable is not set in this terminal. I could not check whether an agent session passes these variables on, so after this works, ask the inspector to run the same command and tell you the result.

## Step 11. Three cheap tests (a few credits in total)

Use the dashboard chat for each.

1. **Spec agent.** Pick `spec-agent` and send: `Write a spec, plan, todo and checklist for adding a --verbose flag to the small project in <a folder you name>.` You should get four files in a `docs/specs/...` folder, and a spec that asks you questions it cannot answer itself.
2. **Confidence script.** Pick `genai-agent` and send: `Should I use RAG or fine-tuning for a support bot? Use your checklist.` The answer must contain the words `confidence`, `coverage` and `open items` copied from a script, not a percentage the model made up. If it gives its own percentage, the skill was not loaded; check Step 6.
3. **The loop.** Pick `orchestrator-agent` and send:
   `Ask go-agent to write and run a tiny Go program that sleeps 150 seconds and then prints done. Check every 60 seconds, up to 4 rounds.`
   You should see lines like `Round 1/4: run <id> go-agent RUNNING ...` about every minute, then a final report. If it ends its turn right after starting the run and prints no round lines, tell me; the prompt override did not work on your version.

## Changing things later

- Edit a file in `crews/devteam`, then run `plan` to see the change, then `apply`.
- `python3 -m crewctl -c crews/devteam status` shows one line per crewmate.
- Remove everything: `python3 -m crewctl -c crews/devteam skills destroy`, then `python3 -m crewctl -c crews/devteam destroy --delete-records`.

## If something goes wrong

| You see | What it means | What to do |
|---|---|---|
| `cannot find 'kirocrew-core' on this machine` | Crew has not written its tool file yet, or left the entry out | Run `python3 -m crewctl crew-servers` and follow it (Step 2) |
| `model ... is not available` or a model error in `models` | The model name or your plan does not match | Do Steps 3 and 4 |
| `exists and was not created by crewctl` (skills) | A skill with that name is already in the folder | Rename the skill in `crews/devteam/skills/<name>.skill.json` (and in the agent files that list it), or remove the old one yourself |
| `changed outside crewctl` | Someone edited a generated file by hand | Look at the file; if you want the config to win, add `--force` (a backup is kept) |
| `state is locked` | An earlier run was cut off | Add `--break-lock` if no other crewctl is running |
| Agents are in `kirocrew agent list` but Crew never picks them | Description and triggers are empty | Step 9 |
| Skills missing in the dashboard | Crew has not re-read the folder yet | Wait 2 minutes, then open the Skills tab |
