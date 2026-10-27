# Captain's Deck — personal experimental fork

**This is an unofficial fork of [deimantasnork/captains-deck](https://github.com/deimantasnork/captains-deck).**
I maintain it as an experiment for personal use and evaluation of these tools.
It is not an official upstream release or an endorsement by the original authors.

Changes explored in this fork:

- A responsive local [web kanban](web/README.md) that mirrors the terminal board on desktop and narrow screens.
- Guarded decision replies with durable receipts, duplicate-submission protection, and separate saved-answer and owner-acknowledgment states.
- Concise delivery summaries, full report/detail views, searchable retained history, and local read markers.
- Clearer task phases, blockers, dependencies, next actions, and activity provenance, with timestamps that preserve recorded precision and identify unknown times.
- Per-installation English/Spanish web localization, English by default, and System/Light/Dark themes.
- Portable web launch/control with documented [platform limits](docs/portability.md); the full Windows backend still requires WSL2.

See [web setup and controls](web/README.md), [contributing and checks](CONTRIBUTING.md), and [reviewed public exports](docs/public-export.md) for details.
Remote second mates use `fm-on.sh` for terminal snapshots; remote web capabilities depend on the backend.

| Terminal shortcut | Action |
| --- | --- |
| `m` | Open Message Firstmate; Enter sends and Esc closes. |

The original [MIT license and copyright notice](LICENSE) and inherited commit history are preserved.
Personal tasks, projects, configuration, and dashboard data are kept outside this repository.
