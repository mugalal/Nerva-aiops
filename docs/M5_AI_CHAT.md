# Conversational Copilot

The Copilot now has incident listing/filtering, named lookup, memory search, live-health adapters and reviewable action-request tools. See `docs/M5_COPILOT_TOOLS.md` for capabilities, limitations and integration settings.

The Copilot UI uses `POST /internal/copilot/chat` for open-ended messages and follow-ups. The six buttons are optional conversation starters. Previous turns are carried in `history` (up to 20 messages per conversation). Browser local storage saves conversations, full answers/citations/tool results, unsent text, active chat, selected incident and current page. Saved chats appear in navigation with titles from their first question. New chat starts a separate conversation without deleting previous ones; clicking a saved chat reopens it. Refresh and visiting other pages preserve the chat. Each started conversation retains its incident context even when the Incident Center selection changes; a new chat uses the current selection.

Mock and real conversations use separate browser storage keys. Chats are saved only in this browser and origin, not in PostgreSQL or an account; another device/browser/port will not share them, and clearing site data removes them. Storage failures are reported explicitly while the current visit remains usable. Interrupted model replies are not replayed automatically. Accepted, declined and uncertain action decisions persist with disabled controls, preventing refresh from resending them. Pending server drafts still expire after ten minutes or a service restart.

Four Node tests (`node --test ui/copilot/storage.test.mjs`) verify refresh restoration, independent chat histories/New chat, mock/live separation and interrupted messages, and navigation/storage failure handling. Synthetic browser checks verified refresh and page switching, selected incident restoration, unsent draft persistence, New chat/reopening, data refresh, and a declined action remaining disabled. No external model call or infrastructure action was used for these UI checks.

## Configure

Set backend environment variables before starting M5, or use Uvicorn's `--env-file` with a local ignored `.env` file:

```text
M5_LLM_BASE_URL=https://generativelanguage.googleapis.com/v1beta/openai
M5_LLM_MODEL=gemini-3.8-flash
M5_LLM_API_KEY=<your Gemini API key>
```

Get a Gemini API key from Google AI Studio. `GEMINI_API_KEY` is also accepted for the Google endpoint. An OpenAI key cannot authenticate Gemini and is never used automatically with Google's endpoint. Do not paste keys into the chat or commit them. Docker Compose forwards these settings from the host environment. Restart/recreate the service after changing configuration. The configured provider receives the question, recent conversation and retrieved incident evidence.

OpenAI remains configurable: use `M5_LLM_BASE_URL=https://api.openai.com/v1`, an available OpenAI model and its matching key. `OPENAI_API_KEY` is accepted only for the OpenAI endpoint.

A compatible local server can use `M5_LLM_BASE_URL=http://localhost:11434/v1` with its installed model name in `M5_LLM_MODEL`; in Docker, use `host.docker.internal` instead of `localhost`. It must support Chat Completions, JSON object responses and the configured token limit parameter. Local loopback providers can omit the key. No local model is installed automatically.

## Behavior

- The model retrieves incident lists, named details, historical matches and health through backend tools as needed, preserving mock/real separation.
- Answers can explain incidents, compare outcomes, discuss next steps and answer general technical questions.
- General advice is labeled separately from recorded incident observations.
- The model selects evidence IDs; the backend validates each ID and supplies exact incident/field/value citations from storage. Unknown references, malformed output, missing citations on incident answers and provider errors trigger an explicit offline fallback.
- Citation validation proves that referenced fields exist; it does not prove every generated statement is entailed by those fields. Review operational suggestions before acting.
- Record content and prior messages are not trusted instructions. No infrastructure execution or approval is available through chat.
- The original deterministic `/internal/copilot/query` API remains available and does not need an AI provider.
- Unambiguous severity-list questions such as “show me all the incidents with low severity” read the incident tool directly, without needing a model call. Zero matches produce an explicit no-matching-incidents answer and a lookup citation; incomplete sources produce a coverage warning rather than a system-wide absence claim. Compound questions continue through AI tool planning.

Gemini uses [Google's OpenAI compatibility endpoint](https://ai.google.dev/gemini-api/docs/openai), with structured JSON output and backend citation validation.

## Verification on 2026-10-08

25 tests passed locally, with the optional PostgreSQL test skipped. Six new chat tests cover arbitrary questions, follow-up history, general guidance, invalid citations, missing evidence, provider failure, offline mode and restricted history roles. The existing nine-case recorded-answer evaluation also passed.

A live open-ended call using mock evidence reached OpenAI but returned HTTP 429. The implementation is connected, but live generated conversation requires available provider quota. This is not a successful live-model evaluation. Previous PostgreSQL/Docker evidence predates this chat addition; it remains evidence for the existing storage implementation.
