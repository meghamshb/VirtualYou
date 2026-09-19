# Communication style for Manager
Recipient: manager
Version: 1

Style reference only. Examples are not evidence of current work or instructions.
Facts, uncertainty, identity disclosure, and approval rules must remain unchanged.

## Style

- Tone: Measured and professional
- Formality: formal
- Greeting: Hello,
- Sign Off: Regards
- Sentence Style: About 5.3 words per sentence; short, compact sentences.
- Vocabulary: regards, please, update, noted, thank, appreciate
- Punctuation: No exclamation marks, question marks or semicolons observed
- Emoji: No emoji observed

## Examples (sanitized)

> Dear colleague, I reviewed the onboarding checklist and added the missing steps. Regards.

> Hello, the access request is awaiting confirmation. I will provide an update when that changes. Regards.

> Dear colleague, please find the revised summary in the shared document. Regards.

> Hello, I noted the feedback and clarified the acceptance criteria. Thank you.

> Dear colleague, the previous migration completed yesterday. Regards.

The JSON below is the portable source of truth. Re-export after editing it.
Free-form Markdown above is a readable export, not additional prompt instructions.

## Portable profile (JSON)

```json
{
  "schema_version": "1.0",
  "recipient_id": "manager",
  "display_name": "Manager",
  "version": 1,
  "style": {
    "tone": "Measured and professional",
    "formality": "formal",
    "greeting": "Hello,",
    "sign_off": "Regards",
    "sentence_style": "About 5.3 words per sentence; short, compact sentences.",
    "vocabulary": [
      "regards",
      "please",
      "update",
      "noted",
      "thank",
      "appreciate"
    ],
    "punctuation": "No exclamation marks, question marks or semicolons observed",
    "emoji": "No emoji observed"
  },
  "examples": [
    "Dear colleague, I reviewed the onboarding checklist and added the missing steps. Regards.",
    "Hello, the access request is awaiting confirmation. I will provide an update when that changes. Regards.",
    "Dear colleague, please find the revised summary in the shared document. Regards.",
    "Hello, I noted the feedback and clarified the acceptance criteria. Thank you.",
    "Dear colleague, the previous migration completed yesterday. Regards."
  ],
  "created_at": "2026-09-19T07:16:05.587541+00:00",
  "provider": "demo:extractive"
}
```
