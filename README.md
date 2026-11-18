# NBA Stats Q&A

Ask questions about NBA player stats in plain English. An LLM turns the question
into SQL, the SQL is safety-checked and run against a Postgres database of real
NBA data, and the answer comes from the query results.

Status: early development.

## Local development

Requirements: [uv](https://docs.astral.sh/uv/) and Docker.

```bash
uv sync                      # create .venv and install dependencies
cp .env.example .env         # local settings (git-ignored)
docker compose up -d         # start local Postgres on localhost:5432

uv run ruff check .          # lint
uv run ruff format .         # auto-format
uv run pytest                # tests
```

## Project layout

```
src/nba_query/   application code
tests/           automated tests
spikes/          throwaway experiments and their findings (see spikes/FINDINGS.md)
```
