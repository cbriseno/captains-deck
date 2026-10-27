# Captain's Deck — personal experimental fork

**This is an unofficial fork of [deimantasnork/captains-deck](https://github.com/deimantasnork/captains-deck).**
I maintain it as an experiment for personal use and evaluation of these tools.
It is not an official upstream release or an endorsement by the original authors.

Changes explored in this fork:

- A responsive local [web kanban and setup](web/README.md) that mirrors the terminal board on desktop and narrow screens.
- Guarded decision replies with durable receipts, duplicate-submission protection, and separate saved-answer and owner-acknowledgment states.
- Concise delivery summaries, full report/detail views, searchable retained history, and local read markers.
- Clearer task phases, blockers, dependencies, next actions, and activity provenance, with timestamps that preserve recorded precision and identify unknown times.
- Per-installation English/Spanish web localization, English by default, and System/Light/Dark themes.
- Portable web launch/control with documented [platform limits](docs/portability.md); the full Windows backend still requires WSL2, with [reviewed exports](docs/public-export.md) for public updates.
