# token-usage

A panel beside the Claude Code chat that shows how many tokens you have used and
what that would cost at Anthropic API list prices. It reads the transcripts Claude
Code keeps on your computer (`~/.claude/projects`), so nothing leaves your machine.

The panel opens when you send a prompt and refreshes after each reply. `/tokens`
opens it by hand. It shows:

- this session's cost, tokens and messages
- today, last 7 days, last 30 days and all time
- daily cost for the last 14 days
- cost by model and by project (last 30 days)

If you use Claude Code on a Pro or Max subscription, the cost is what the usage
would cost on the API, not what you were billed.

## Install

At the prompt of a Claude Code terminal session:

```
/plugin install token-usage --marketplace arneanne/claude-token-usage
```

Answer `y` to add the marketplace, then pick a scope (user scope loads it in
every session).

From a shell:

```bash
claude plugin install token-usage --marketplace arneanne/claude-token-usage
```

In the desktop app, where `/plugin` does not run: click + next to the prompt box,
then Plugins, Add marketplace with `arneanne/claude-token-usage`, then Add plugin.

Requires Python 3.9 or newer as `python3` on your PATH.

## Command line

The counting script also works on its own:

```bash
python3 scripts/cc_tokens.py --by model      # or day, month, project, session
python3 scripts/cc_tokens.py --days 7
python3 scripts/cc_tokens.py --prices        # the price table used
```

Prices are in the `PRICES` table at the top of `scripts/cc_tokens.py`, taken from
https://platform.claude.com/docs/en/about-claude/pricing; update it if they change.
