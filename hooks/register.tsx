import { atom, read, update } from 'claude-code'
import type { EngineInterface, Register } from 'claude-code'

import type { Row, Summary, Totals } from '../types'

const PANE = 'token-usage'
const TITLE = 'Token usage'
// The counting script ships in the plugin; see `python3 scripts/cc_tokens.py --help`.
const SCRIPT = 'scripts/cc_tokens.py'

const summary = atom({ plugin: 'token-usage', key: 'summary' } as const, null)
const error = atom({ plugin: 'token-usage', key: 'error' } as const, null)

// How to start Python: `python3` on macOS and Linux; on Windows usually `python`
// or the `py` launcher (there `python3` is often a stub that opens the Store).
const PYTHONS = [['python3'], ['python'], ['py', '-3']]
let python: string[] | null = null

async function refresh($: EngineInterface) {
  const id = await $.session.id()
  const script = `${$.plugin.root}/${SCRIPT}`
  let failure = 'Python 3.9+ was not found (tried python3, python, py -3)'
  for (const cmd of python ? [python] : PYTHONS) {
    try {
      const run = await $.process.run([...cmd, script, '--summary', id], { timeoutMs: 60_000 })
      if (run.exitCode !== 0) {
        failure = `${cmd.join(' ')} exited ${run.exitCode}: ${run.stderr.slice(0, 300)}`
        continue
      }
      const parsed = JSON.parse(run.stdout) as Summary
      python = cmd
      await update($, summary, () => parsed)
      await update($, error, () => null)
      return
    } catch (err) {
      failure = `Could not run ${cmd.join(' ')}: ${String(err)}`
    }
  }
  python = null
  await update($, error, () => failure)
}

const usd = (x: number) => `$${x.toFixed(2)}`
const tokens = (n: number) =>
  n >= 1e6 ? `${(n / 1e6).toFixed(1)}M` : n >= 1e3 ? `${(n / 1e3).toFixed(1)}K` : String(n)
const baseName = (p = '') => p.split(/[\\/]/).filter(Boolean).pop() ?? p

function sparkline(days: { cost: number }[]) {
  const bars = '▁▂▃▄▅▆▇█'
  const max = Math.max(...days.map(d => d.cost), 0.01)
  return days.map(d => (d.cost === 0 ? '·' : bars[Math.min(7, Math.floor((d.cost / max) * 7.99))])).join('')
}

function asMarkdown(s: Summary) {
  const period = (name: string, t: Totals) =>
    `| ${name} | ${usd(t.cost)} | ${tokens(t.total)} | ${t.messages} |`
  const row = (label: string, r: Row) => `| ${label} | ${usd(r.cost)} | ${tokens(r.total)} |`

  return [
    `### This session: ${usd(s.session.cost)}`,
    `${tokens(s.session.total)} tokens · ${s.session.messages} messages · ${tokens(s.session.output)} output`,
    '',
    '| Period | Cost | Tokens | Msgs |',
    '| --- | ---: | ---: | ---: |',
    period('Today', s.today),
    period('Last 7 days', s.week),
    period('Last 30 days', s.month),
    period('All time', s.all),
    '',
    `**Last 14 days** \`${sparkline(s.daily)}\` (max ${usd(Math.max(...s.daily.map(d => d.cost)))}/day)`,
    '',
    '#### By model (30 days)',
    '| Model | Cost | Tokens |',
    '| --- | ---: | ---: |',
    ...s.by_model.map(r => row((r.model ?? '').replace(/^claude-/, ''), r)),
    '',
    '#### By project (30 days)',
    '| Project | Cost | Tokens |',
    '| --- | ---: | ---: |',
    ...s.by_project.map(r => row(baseName(r.project), r)),
    '',
    `_Estimated at API list prices. Updated ${s.generated.slice(11, 16)}._`,
  ].join('\n')
}

export const register: Register = on => {
  on('session.start', async ($, e, next) => {
    await $.command.register({
      name: 'tokens',
      description: 'Show Claude Code token usage and cost in a pane',
    })
    void refresh($)

    return next(e)
  })

  on('command.run', { command: 'tokens' }, async $ => {
    await $.ui.open({ id: PANE, title: TITLE })
    await refresh($)

    return { text: 'Token usage pane opened.' }
  })

  // Each prompt opens the pane (a no-op when it is already open) and refreshes it.
  on('prompt.submit', async ($, e, next) => {
    const result = await next(e)
    try {
      await $.ui.open({ id: PANE, title: TITLE })
      await refresh($)
    } catch {
      // Never let the panel get in the way of the prompt.
    }

    return result
  })

  // Refresh again once the reply is in, so its tokens are counted.
  on('turn.complete', async ($, e, next) => {
    const result = await next(e)
    await refresh($)

    return result
  })

  on('ui.render', { component: 'Pane', requestId: PANE }, async ($, e) => {
    const { Box, Text, Button, Markdown } = $.ui.resolve(e)
    const s = await read($, summary)
    const err = await read($, error)

    return (
      <Box flexDirection="column">
        {err !== null && <Text color="red">{err}</Text>}
        {s === null ? <Text dimColor>Counting tokens…</Text> : <Markdown text={asMarkdown(s)} />}
        <Box>
          <Button key="refresh" label="Refresh" onPress={() => refresh($)} />
        </Box>
      </Box>
    )
  })
}
