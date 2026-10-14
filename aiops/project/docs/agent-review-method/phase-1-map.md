# Phase 1: Map the system as built

Goal: an accurate map of the graph, state, agents, prompts, tools, KB and quality infrastructure. Don't judge anything yet.

Deliverable: `docs/agent-review/01-system-map.md` (Template T1 in `D-templates.md`).

## 1.1 Set up

- Read `docs/agent-review-method/inputs.md`.
- In manual mode, create `docs/agent-review/_progress.md` from Template T0, with every step ID of Phases 1 to 5 as an unchecked box.
- Check REFERENCE_ROOT by listing `<REFERENCE_ROOT>/OpenAI/Codex` and `<REFERENCE_ROOT>/Anthropic/claude-code`. If either is missing or unreadable, stop and tell me.
- Run `python3 -I tools/agent_review/check_citations.py --self-test`. It should print `self-test ok`. If it doesn't, tell me.
- Done when REFERENCE_ROOT is readable, the self-test passes, and (in manual mode) the progress file exists.

## 1.2 Get the project overview

- List the project tree two levels deep, excluding dependency and build folders.
- Identify the language, package manager and lockfile (`pyproject.toml`, `poetry.lock`, `uv.lock`, `requirements*.txt`, `package.json`, `pnpm-lock.yaml`, and so on).
- Record the versions of `langgraph`, `langchain` and `langchain-core`, and of the provider packages (`langchain-anthropic`, `langchain-openai`, `langchain-aws`, `langchain-google-*`, `langchain-ollama`, and so on).
- Read the `README`, `.kiro/steering/*` (except `agent-review.md`), and any `AGENTS.md` or `CLAUDE.md`.
- Find the entry points: where the graph is compiled and invoked, such as an API route, CLI, alert webhook, chat bot or scheduler.
- Done when the stack, the versions and the entry points are recorded with `file:line`.

## 1.3 Find the graphs

- Python search hints: `StateGraph(`, `add_node(`, `add_edge(`, `add_conditional_edges(`, `set_entry_point(`, `START`, `END`, `Send(`, `Command(`, `interrupt(`, `.compile(`, `checkpointer`, `recursion_limit`, `create_react_agent(`, `create_supervisor(`, `create_swarm(`, `AgentExecutor(`, `create_tool_calling_agent(`.
- TypeScript search hints: `new StateGraph(`, `.addNode(`, `.addEdge(`, `.addConditionalEdges(`, `createReactAgent(`.
- For each graph, record:
  - its nodes (name, function, `file:line`) and edges;
  - for each conditional edge, the routing function, read and summarized in one sentence saying what decides the route;
  - subgraphs, `Send` fan-out, `interrupt` points, checkpointer type, recursion limit, retry policies and timeouts.
- Draw each graph in Mermaid.
- Done when every node in the code appears in a diagram.

## 1.4 Map the state

- Search hints: `TypedDict`, `BaseModel`, `MessagesState`, `Annotated[`, `add_messages`, `operator.add`.
- For each field, record its type, its reducer, which nodes write it, and which read it.
- Write down what passes from the orchestrator to the specialists and back: raw tool output, summaries, or references to stored artifacts.

## 1.5 Map the agents

For every node that calls a model, record:

- **Model and provider.** Search hints: `ChatAnthropic(`, `ChatOpenAI(`, `AzureChatOpenAI(`, `ChatBedrock`, `ChatBedrockConverse(`, `ChatVertexAI(`, `ChatGoogleGenerativeAI(`, `ChatOllama(`, `init_chat_model(`.
- **Parameters:** temperature, max tokens, thinking, effort or reasoning settings, and parallel tool calls.
- **How it's built:** a prebuilt agent or a custom node.
- **Its tools:** `bind_tools(`, `tools=[`.
- **Its prompt:** where it's defined.
- **Its structured output:** `with_structured_output(`, `PydanticOutputParser`, `JsonOutputParser`, `response_format=`.

## 1.6 Inventory the prompt surface

- Search hints: `SystemMessage(`, `HumanMessage(`, `ChatPromptTemplate`, `PromptTemplate`, `MessagesPlaceholder`, `"You are`, `prompt=`, `state_modifier=`, `system_prompt`, a `prompts/` folder, and files the code loads (`open(`, `read_text(`, `load_prompt(`) ending in `.prompt`, `.j2`, `.jinja`, `.txt`, `.md` or `.yaml`.
- For each prompt, record:
  - an ID (PR-01, PR-02, and so on) and its location;
  - which agent or node uses it;
  - how it's assembled: static, f-string, template, or conditional sections;
  - its rough size in lines, and its dynamic variables.
- Tool descriptions and schema field descriptions reach the model too, so list them as prompts.
- Don't judge anything yet.

## 1.7 Map the tools

- Search hints:
  - tool definitions: `@tool`, `StructuredTool`, `BaseTool`, `Tool(`, `ToolNode(`;
  - VictoriaLogs: `/select/logsql/`;
  - VictoriaMetrics: `/api/v1/query`, `/api/v1/query_range`, `/api/v1/series`, `/api/v1/labels`;
  - Tempo: `/api/search`, `/api/traces/`, `/api/v2/search/tags`, `/api/metrics/`;
  - GitLab: `gitlab.Gitlab(`, `/api/v4/`, `repository/compare`, `merge_requests`, `deployments`, `pipelines`;
  - HTTP clients: `httpx`, `requests`, `aiohttp`.
- For each tool, record:
  - its name, its agent, and the backend and endpoint it calls;
  - its parameters, with defaults and bounds;
  - who builds the query: the model writes a raw query, or the model fills parameters;
  - time handling: absolute or relative, and which time zone;
  - output shaping: limits, truncation, summarizing, size caps;
  - error handling: does it raise, return empty, or return a message?
  - retries and timeouts;
  - credentials (environment variable names only, never values), and whether it is read-only.

## 1.8 Map the KB

- Search hints: `as_retriever(`, `similarity_search(`, `VectorStore`, `Chroma`, `FAISS`, `PGVector`, `Qdrant`, `Weaviate`, `Milvus`, `OpenSearch`, `Elasticsearch`, `Embeddings`, `TextSplitter`, `Document(`, `kb`, `knowledge`, `runbook`.
- Record:
  - the sources it ingests, the chunking and the embedding model;
  - retrieval: k, filters, and whether it uses keyword or hybrid search;
  - which agents call it, when, and how results enter prompts;
  - freshness and the update process;
  - write-back after incidents.

## 1.9 Map the quality infrastructure

- Search hints: `LANGSMITH_`, `LANGCHAIN_TRACING`, `langfuse`, `CallbackHandler`, `opentelemetry`, `tests/`, `pytest`, `evaluate(`, `create_dataset`, `fixtures`, `.gitlab-ci.yml`.
- Record the tracing, token and cost accounting, tests that cover agents, eval datasets, fixtures and CI checks.

## 1.10 Write `01-system-map.md`

- Use Template T1.
- Include "Mismatches with the user's description" and "Open questions". Each question needs options and a recommended default.
- Run `python3 -I tools/agent_review/check_citations.py --only docs/agent-review/01-system-map.md` and fix every problem it reports.

## 1.11 Stop for my confirmation

- Send the end-of-session report.
- When I answer, record my answers in `_progress.md` under "Decisions and answers", fix the map, tick 1.11, and stop.
- Everything after this phase depends on the map, so don't start Phase 2 in the same session.
