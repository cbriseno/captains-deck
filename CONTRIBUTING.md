# Contributing

Thanks for wanting to help improve Captain's Deck.

Captain's Deck exists so captains can see fleet-wide flow and unblock agents without tab-juggling—while [Firstmate](https://github.com/kunchenguid/firstmate) handles the heavy lifting of shipping finished work (PRs, branch sync, conflicts, validation, optional yolo merge).
If you use this board in anger, your reports and PRs make that story clearer for everyone.

Bug reports, feature ideas, and pull requests are welcome.
You do not need maintainer access to participate.

## Report a problem or suggest a change

Use the Issues tab of the fork you are running on GitHub.
Use the templates when they fit, or choose **Open a blank issue** if none match.

Please include:

- Herdr and plugin versions (`herdr --version`, `herdr plugin list`)
- Whether you use captain-only or captain + secondmate homes
- Steps to reproduce (for bugs) or the workflow you want (for features)
- Screenshots or terminal output when they clarify the report

## Pull request workflow

1. Fork [cbriseno/captains-deck](https://github.com/cbriseno/captains-deck) on GitHub.
2. Clone your fork (or add it as a remote) and create a branch for your change.
3. Install the plugin locally for manual testing:

   ```sh
   herdr plugin link /path/to/your/captains-deck-clone
   herdr plugin enable herdr-firstmate-flow
   ```

4. Run the checks before you push:

   ```sh
   npm ci --prefix tests --ignore-scripts --no-audit --no-fund
   export PATH="$PWD/tests/node_modules/.bin:$PATH"
   python3 tests/test_flow_decision_dialog.py
   python3 tests/test_open_scripts.py
   python3 tests/test_flow_homes.py
   python3 -m pytest tests/ -q
   python3 scripts/fm_contract_check.py --home tests/fixtures/home
   ```

   `pytest` is required for the test suite; install it with `pip install pytest` if needed.
   History fixtures also require Node.js 20 or newer and the locked `tasks-axi` canonical reader installed above.
   This test dependency stays inside the checkout and does not install a global CLI.
   The contract check runs against the recorded fixture home here; point `--home` at a real Firstmate home to check the live surfaces after an `updatefirstmate`.

5. Open a pull request against `main` with a short summary and test notes.

Maintainers review PRs as time allows.
Smaller, focused changes are easier to land than large rewrites.

## Repo conventions

- `scripts/flow_tui.py` owns the Captain's Deck TUI; keep bash helpers in `scripts/`.
- `scripts/fm_contract_check.py` owns the Firstmate contract check; keep it and its fixture assertions aligned when you change a surface that it covers.
- `herdr-plugin.toml` is the Herdr marketplace manifest; bump `version` when you ship user-visible behavior.
- In Markdown, put each full sentence on its own line (same style as [Firstmate](https://github.com/kunchenguid/firstmate)).
- `README.md` stays a concise overview; route long detail to comments in code or issue discussion unless it belongs in the README.

## Questions

Use your fork’s Issues tab with the **Question** template, or a blank issue if you prefer.

## Public fork updates

Keep private runtime configuration outside the checkout.
Use the [reviewed export workflow](docs/public-export.md) when transferring changes from an operational repository to a public fork.
Never publish its local branches or history wholesale.
The web needs no dependency installation: `python3 web/serve.py run`.
Run `python3 -m unittest discover -s web/tests` for the HTTP security, producer and native launcher checks.
