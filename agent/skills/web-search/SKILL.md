---
skill: web-search
version: 1.0.0
author: clawdcontext-os
capabilities:
  - net:api.duckduckgo.com
  - net:api.bing.microsoft.com
  - net:serpapi.com
  - file_write:/workspace/agent/search-results.md
rate_limit: 10/minute
token_budget: 2000
signature: "UNSIGNED — run `make sign` to generate"
signed_by: ""
signed_at: ""
---

# SKILL.md — Web Search

## Purpose

Search the web for information relevant to the current task.
Returns structured results (title, URL, snippet) to the agent context.

## When to Load

- Agent needs external information not available in workspace
- User explicitly asks to "search", "look up", or "find"
- Planner identifies a research step in todo.md

## Instructions

1. Construct a search query from the user's intent (max 10 words)
2. Call the search API using the granted network capability
3. Parse top 5 results into structured format
4. Write results to `/workspace/agent/search-results.md`
5. Summarize key findings in response (max 200 tokens)

## Constraints

- **DO NOT** follow URLs to arbitrary websites (only search API endpoints)
- **DO NOT** cache results beyond the current session
- **DO NOT** include raw HTML in output
- **DO NOT** search for credentials, API keys, or personal information
- **RESPECT** rate limit: 10 requests per minute maximum

## Output Format

```markdown
## Search Results: "{query}"

1. **{title}** — [{domain}]({url})
   > {snippet}

2. **{title}** — [{domain}]({url})
   > {snippet}

(max 5 results)
```

## Error Handling

| Error | Action |
|-------|--------|
| Rate limit exceeded | Wait 60s, retry once, then fail gracefully |
| Network unreachable | Report "search unavailable", continue without |
| No results | Report "no results found for: {query}" |
| API key missing | Report "search API not configured" |
