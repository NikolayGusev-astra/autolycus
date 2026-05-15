# API Key Format Quick Reference

When diagnosing auth failures, check key prefix BEFORE concluding cause:

| Prefix | Provider | Example |
|--------|----------|---------|
| `sk-or-*` | OpenRouter | `sk-or-v1-71c...` |
| `csk-*` | Cerebras | `csk-j2538...` |
| `sk-*` | OpenAI | `sk-proj-...` |
| `sk-ant-*` | Anthropic | `sk-ant-oat01-...` |
| `gsk_*` | Groq | `gsk_...` |

## Common Mismatch Patterns

**Cerebras key on OpenRouter endpoint:**
- `api_key: csk-j2538...` + `base_url: https://openrouter.ai/api/v1`
- Result: HTTP 402 (not 401!) — OpenRouter accepts the request format but Cerebras account has no credits on OpenRouter
- Looks like: "credits exhausted" or "rate limited"
- Actually: wrong key type for this endpoint

**Empty key in auxiliary config:**
- `auxiliary.vision.provider: groq` + `api_key: ''`
- Subagents trying to use auxiliary models → 402 or 401
- Main agent unaffected (uses main model key from .env)

## Diagnostic Steps

1. `grep 'api_key' ~/.hermes/config.yaml` — find all keys in config
2. `grep 'OPENROUTER_API_KEY\|API_KEY' ~/.hermes/.env` — find all keys in env
3. Compare prefix with base_url for each
4. Check `journalctl` for `[subagent-N]` tag — are errors from subagents only?
5. If main agent responds fine but subagents fail → auxiliary config issue, not main key
