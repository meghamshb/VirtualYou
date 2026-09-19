# Design specification

Reference: full primary screen 1505 × 1045. White canvas, cool grey sidebar,
green #22634a, text #151c26, muted #6d7786, border #e3e7eb. No gradients or card
grid. SF/system sans. 46px title and 22px introduction at wide desktop sizes; smaller responsive
type below 1300px. 15px controls, 13px supporting text. Sidebar 270px on wide
screens, 232px below 1300px; setup content max 1200px including padding. 8/16/24/32/48 spacing; 8px button radius.
Lucide outline navigation; brand marks code-native vectors. No raster UI controls.

Composition: title strip, sidebar/breadcrumb, headline, three-step progress, open
integration rows, privacy note, footer. Views: Setup, Integrations, Projects,
Approvals (list/detail + source quotes), Activity, Diagnostics, Settings.
Narrow screens: compact navigation, stacked approvals, reduced title scale.

Intentional functional differences: visibly label Preview; never claim collector
Ready without health checks; generic identity until connected. Offline and missing
hosted service states must be actionable. These supersede illustrative concept data.
