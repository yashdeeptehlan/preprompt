# PrePrompt Skill

Use this skill to manually apply PrePrompt-style prompt optimization in any AI tool that doesn't support MCP hooks (e.g. ChatGPT, Gemini, Zed without MCP, raw API calls).

## When to activate

Score the incoming prompt mentally using these rules. If the total exceeds 38, optimize it.

**Add points:**
- Contains vague/ambiguous verbs ("handle", "manage", "improve", "refactor", "clean up"): +8 per verb, max +25
- Multiple distinct requirements in one prompt (e.g. "do X and also Y and handle Z"): +12 per extra requirement, max +30
- Deep conversation turn (turn > 2): +5 per turn beyond 2, max +15
- Describes a code task but has no format hint (no "return JSON", "list steps", etc.): +15

**Subtract points:**
- Short prompt (< 8 words): −20
- Starts with "what is / what are / what does": −15
- Already structured (has numbered steps, bullet points, or code blocks): −15
- Casual opener ("hey", "can you", "please", "just", "quick question"): −25

## How to optimize

When the score ≥ 38, rewrite the prompt using this process:

1. **Identify the core task** — strip filler words and casual framing.
2. **Make the goal concrete** — replace vague verbs with specific actions ("add error handling for X" instead of "handle errors").
3. **Separate concerns** — if there are multiple requirements, list them as numbered sub-tasks.
4. **Add a format hint** — specify the expected output format (code block, numbered list, prose, JSON schema, etc.) if it helps.
5. **Preserve intent** — do not add requirements the user didn't ask for.

## Output format

Return a JSON object:
```json
{
  "optimized_prompt": "<rewritten prompt>",
  "classifier_score": <integer>,
  "was_intercepted": true
}
```

If the score is below 38, return:
```json
{
  "optimized_prompt": "<original prompt unchanged>",
  "classifier_score": <integer>,
  "was_intercepted": false
}
```

## Example

**Input:** "refactor this service to handle errors and manage the retry logic and also add some tests"

**Score:** vague verbs ("handle", "manage") = +16, multiple requirements (errors + retry + tests) = +24 → **total: 40** → optimize

**Output:**
```json
{
  "optimized_prompt": "Refactor the service with these changes:\n1. Replace bare except clauses with specific exception types and log each error with context.\n2. Implement exponential backoff retry logic with configurable max_attempts and base_delay.\n3. Add unit tests covering: successful path, all error branches, and retry exhaustion.\nReturn updated source files only.",
  "classifier_score": 40,
  "was_intercepted": true
}
```
