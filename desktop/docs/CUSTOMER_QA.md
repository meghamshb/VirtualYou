# Customer onboarding verification

This is a functional implementation, tested locally against controlled external
provider responses. It is not a report of a live deployment or real authorization.

- OAuth/relay tests: pairing, single-use grants, browser cookie/state binding,
  cancellation, expired state, missing scopes, wrong Slack owner, GitHub PKCE,
  Jira and Google exchanges, rotating refresh tokens and invalid refresh handling.
- Backend flow uses the real existing backend and workflow: sanitized evidence →
  cited draft → explicit approval → delivery receipt. External LLM/Slack responses
  are fixtures. Completion is refused until the setup test has been delivered.
- Desktop tests check narrow IPC, no credentials returned to renderer, rejected
  foreign authorization origins, encryption boundary, and no uncertain-send retry.
- Native collector executable ran on a temporary fixture Git repository without
  invoking Python. It returned summaries with no raw patches/prompts/source paths.
- Native Electron opens the customer screen and retains the developer preview;
  the previous wizard persistence/approval smoke still passes.
- Browser/IAB exercised customer project selection, collection, draft, confirmation
  and completion through an explicit IPC fixture. No real connection was claimed.
- Existing Slack suite: 122 passed. Audio dependency was installed in an isolated
  test environment; no live backend environment or service was changed.
- TypeScript, ESLint, Python lint and production desktop build checked.
- Docker container launch was not tested: local Docker daemon is unavailable.
- Public callback checks and real provider authorization remain blocked by missing
  hosting/domain and operator provider configuration.

## Visual check

Compared the original 1505×1045 concept with the customer screen using view_image.
Browser/IAB screenshots were captured at 1505×1045. This screen intentionally
changes the original three-step preview to a real account-first flow:

| Point | Result |
| --- | --- |
| Palette | White canvas, muted grey chrome, original green primary actions |
| Typography | Same system font, large headline and readable control sizing |
| Headline | “Make room for your real work.” retained |
| Icons | Code-native outline diamond/shield and arrow; no raster controls |
| Layout | Intentional focused onboarding instead of a sidebar before login |
| Content | Actual permissions/pairing and collector status replace sample integrations |
| Action hierarchy | One Connect Slack primary action before account pairing |

The copy additions are required by the real customer flow. Reference appearance
was retained where applicable; layout differences are intentional and this is not
claimed to be a pixel-identical reproduction of the earlier preview concept.

Final suite counts: 241 backend/ingestion/hosted tests passed (including 18 hosted
cases), 122 Slack tests passed, and 16 desktop tests passed. Customer narrow layout
checked at 390×844 with no horizontal page overflow. Browser connection states
were fixtures; native unconfigured service state remains honest.

The additional GitHub/Jira integration test confirms both sources appear in the
actual backend draft, approval delivers through the existing gateway, and removing
a Jira selection retracts that evidence. Provider HTTP calls remain fixtures.
