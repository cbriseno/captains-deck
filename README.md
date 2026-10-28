# Captain's Deck — personal experimental fork

**This is an unofficial fork of [deimantasnork/captains-deck](https://github.com/deimantasnork/captains-deck).**
I maintain it as an experiment for personal use and evaluation of these tools.
It is not an official upstream release or an endorsement by the original authors.

Changes explored in this fork:

- A responsive local [web kanban and setup](web/README.md) that mirrors the terminal board on desktop and narrow screens.
- Guarded decision replies with durable receipts, duplicate-submission protection, and separate saved-answer and owner-acknowledgment states.
- Concise delivery summaries, full report/detail views, searchable retained history, and local read markers.
- Clearer task phases, blockers, dependencies, next actions, and activity provenance, with timestamps that preserve recorded precision and identify unknown times.
- Per-installation English/Spanish localization for the web and terminal boards, English by default, and System/Light/Dark web themes.
- Portable web launch/control with documented [platform limits](docs/portability.md); the full Windows backend still requires WSL2, with [reviewed exports](docs/public-export.md) for public updates.

## Install

You need [Herdr](https://herdr.dev) 0.9.0 or newer, Bash, Python 3.11 or newer, and a Firstmate home from the [Firstmate fork](https://github.com/cbriseno/firstmate); its checkout root is the home.
Install the plugin from GitHub; Herdr previews the commands it registers, and `--yes` skips that confirmation:

```sh
herdr plugin install cbriseno/captains-deck
```

To run your own checkout instead, use `herdr plugin link /absolute/path/to/captains-deck`.
Either way the plugin id is `herdr-firstmate-flow`, the same id the upstream Captain's Deck uses, so only one of them can be installed at a time.
A link, or an install over a GitHub install, replaces the registered one; Herdr refuses to install over a linked checkout until you run `herdr plugin unlink herdr-firstmate-flow`.
The config directory below is shared and survives the switch.

Point the deck at your Firstmate home:

```sh
printf '%s\n' /absolute/path/to/firstmate > "$(herdr plugin config-dir herdr-firstmate-flow)/fm_home"
```

The `fm_home` reader skips blank lines and `#` comments and uses the first remaining line as the home path.
For several homes, write one `label=/absolute/path` line per home to `homes.conf` in that same directory instead.

The board is in English by default.
To show it in Spanish, write a language to `language` in that same directory, or set `CD_WEB_LANGUAGE` in the environment Herdr runs plugins with, which takes precedence:

```sh
printf '%s\n' es-MX > "$(herdr plugin config-dir herdr-firstmate-flow)/language"
```

It accepts the same values as the [web language setting](web/README.md#language-and-appearance) and applies the next time the board opens.

Open the board with the plugin's **Open captain's deck** (full workspace) or **Open flow board** (overlay) action.
From a shell inside Herdr that is:

```sh
herdr plugin action invoke herdr-firstmate-flow.open-captain-deck
```

The terminal board shows **Loading board…** at startup and **Loading this crew…** while waiting for an uncached crew snapshot.

To bind it to a key, add a `[[keys.command]]` entry with `type = "plugin_action"` and `command = "herdr-firstmate-flow.open-captain-deck"` to Herdr's `config.toml`.
On the board, `?` lists the keys, and `m` messages the Firstmate primary running in a Herdr pane whose working directory is the home.

The local web kanban runs from any checkout of this repository, needs no installation, and reads the same `fm_home` or `homes.conf`:

```sh
python3 web/serve.py run
```

Then open `http://127.0.0.1:8787/`; the [web setup](web/README.md) covers other ports, languages, and running it as a service.
