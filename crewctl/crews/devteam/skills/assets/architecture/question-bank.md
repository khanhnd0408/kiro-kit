# Question bank for architecture interviews

Ask in rounds of 3 to 5 questions, in the user's language. Start with the topic that unlocks most others. After each answer, write it into the context file and run `context_gate.py check`. Keep asking until the gate says ready. Never answer your own question with an assumption; if the user says "I don't know", record it as an open risk and propose how to find out.

## Round 1: purpose and scale
- What problem does this solve, and who uses it? What does success look like in 6 months?
- How many users and requests per second today, at peak, and in a year? Any spikes (campaigns, end of month)?
- What is the cost of being down for an hour? Of losing data?

## Round 2: data
- What are the main entities and how do they relate? Which are read-heavy, write-heavy?
- How big is the data now and in a year? How long must it be kept? Who may see which parts?
- Where must reads be strongly consistent, and where is stale data acceptable (and for how long)?

## Round 3: integrations and constraints
- Which external systems must it call or be called by? Who controls their contracts, rate limits and outages?
- Which technologies are required, forbidden, or already licensed? Why?
- What exists today that must be kept or migrated?

## Round 4: team and operations
- How many engineers, in which languages, with what operations experience? Who is on call?
- Where does it run (cloud, on-premises), who operates the platform, how often will you deploy, how do you roll back?
- What must be observable: logs, metrics, traces, business events, audit trail?

## Round 5: security, compliance, money, time
- What data is sensitive? Which regulations or audits apply? Authentication and authorization model?
- Budget (build and run), deadline, and what can be cut first?

## Questions that expose hidden requirements
- "What is the worst thing a user could do with this?"
- "What will you regret not having asked in a year?"
- "If traffic is ten times what you said, what breaks first for you?"
