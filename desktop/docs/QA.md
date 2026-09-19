> Historical UI milestone document. See [Customer deployment](CUSTOMER_DEPLOYMENT.md) for the current implementation and activation requirements.

# Desktop UI verification — 20 September 2026

Base: committed develop 092151c. Changes are isolated to desktop/ and ignore rules.
Existing backend/Slack source and other working checkouts remain untouched.

## Functional evidence

- Production TypeScript/Vite/Electron build and ESLint checked.
- Unit tests cover wizard guards, immutable state, stale/duplicate approval,
  pause, secret-free diagnostics, validation, exact main-frame authorization,
  encrypted credential-store boundary and refusal of plaintext fallback.
- Backend adapter checks authorization headers, POST refresh, approval failure
  preventing delivery, and no retry of an uncertain send. External HTTP is faked;
  no live Slack delivery was performed.
- Actual Electron smoke passes: welcome → example Slack → project → review →
  restart → persisted completion → sample approval. Renderer has the narrow
  preload bridge and no Node require access. Temporary user data is removed.
- Browser flow passes at 1505×1045. Escape closes a connection dialog and returns
  focus. Sample approval requires confirmation; reload preserves completion.
- At 390×844 there is no document horizontal overflow. Navigation scrolls within
  its own row. Native window minimum width is 780px.
- macOS arm64 unpacked application packaging passes without distribution signing.
  It is a development build, not a notarized customer installer.

## Visual fidelity ledger

Compared generated concept and final setup screenshot side by side using image
inspection at the same 1505px width. The implementation is semantic HTML/CSS/SVG;
no screenshot is used as a UI surface.

| Reference anchor | Result |
| --- | --- |
| Cool grey sidebar and white canvas | Matched, with 270px wide sidebar |
| Large headline and restrained green palette | Matched; system font differs slightly from concept |
| Three numbered steps with connecting rules | Matched; state is interactive and validated |
| Four open integration rows with aligned actions | Matched; code-native marks differ from generated logos |
| Privacy note and bottom Continue action | Matched, with functional guard before advancement |
| Bottom Diagnostics/Settings navigation | Matched on desktop, compact navigation at narrow widths |
| Identity and collector status | Intentional deviation: generic identity and explicit preview status |
| Window chrome | Native Electron chrome; browser screenshot does not fake macOS controls |

The concept's unverified “Ready” status is replaced by “Preview workspace”, and a
persistent no-send label was added. These are intentional truthfulness changes.
Screenshots accompany the handoff: setup, approvals and narrow layout.

## Remaining production work

Hosted OAuth/device pairing, central provider app configuration, bundled Python
collector and lifecycle management, full DM/group approval queue adapter, real
project/source selection and signed/notarized distribution. The local adapter
only exposes existing report endpoints. Preview connections are not live OAuth.

## Test results

- Desktop: 12 unit tests passed; lint/typecheck/build passed; native smoke passed.
- Existing backend/MCP suite: 215 passed, 8 skipped (223 collected).
- Slack suite could not be validated cleanly with the available runtimes. The
  existing Python 3.13 environment is explicitly rejected by the backend for its
  SQLite 3.51.0/1 deadlock version (30 failures, 65 setup errors, 27 passes).
  Python 3.12 avoids that guard but lacks the full Slack/Agents dependency stack;
  an isolated dependency attempt remained blocked during collection. No backend
  guard was bypassed and no live environment was changed to force a pass.
