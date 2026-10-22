import { expect, test } from 'claude-code/testing'

const T = {"input":1,"output":2,"cache_write":3,"cache_read":4,"messages":5,"total":10,"cost":2.91}
const SUMMARY = {
  generated: '2026-10-22 16:45:00',
  session: T, today: T, week: T, month: T, all: T,
  daily: [{ day: '2026-10-22', cost: 2.91 }],
  by_model: [{ ...T, model: 'claude-opus-5-5' }],
  by_project: [{ ...T, project: '/home/me/code/myapp' }],
}

const PANE = {
  plugin: 'token-usage',
  component: 'Pane',
  requestId: 'token-usage',
  props: {
    title: 'Token usage',
    isFocused: false,
    bodyColumns: 40,
    placement: 'dock',
    scroll: { offset: 0, bodyRows: 20 },
    view: {},
  },
} as const

test('the pane shows the session cost from cc_tokens.py', async ($, on) => {
  on('command.register', () => ({ value: { command: 'tokens' } }))
  on('ui.open', () => ({ value: { isPlaced: true } }))
  on('session.id', () => ({ value: 'abc' }))
  on('process.run', () => ({ value: { exitCode: 0, stdout: JSON.stringify(SUMMARY) } }))
  on('session.start', (_, e) => ({ cwd: e.cwd }))
  on('command.run', () => ({ text: '' }))

  await $.session.start({ cwd: '/', surface: 'desktop', isInteractive: true })
  await $.command.run({ command: 'tokens' })

  for (const surface of ['terminal', 'desktop'] as const) {
    const ui = await $.ui.mount({ ...PANE, surface })
    const md = await ui.find({ type: 'Markdown' })
    expect(md?.text).toContain('This session: $2.91')
    expect(md?.text).toContain('myapp')
    await ui.unmount()
  }
})

test('on Windows it falls back from python3 to python, and shortens Windows paths', async ($, on) => {
  const tried: string[] = []
  const winSummary = {
    ...SUMMARY,
    by_project: [{ ...T, project: 'C:\\Users\\me\\code\\winapp' }],
  }
  on('command.register', () => ({ value: { command: 'tokens' } }))
  on('ui.open', () => ({ value: { isPlaced: true } }))
  on('session.id', () => ({ value: 'abc' }))
  on('process.run', (_, e) => {
    tried.push(e.argv[0] ?? '')
    return e.argv[0] === 'python3'
      ? { value: { exitCode: 9009, stdout: '', stderr: 'Python was not found' } }
      : { value: { exitCode: 0, stdout: JSON.stringify(winSummary), stderr: '' } }
  })
  on('session.start', (_, e) => ({ cwd: e.cwd }))
  on('command.run', () => ({ text: '' }))

  await $.session.start({ cwd: '/', surface: 'desktop', isInteractive: true })
  await $.command.run({ command: 'tokens' })

  expect(tried).toContain('python')
  const ui = await $.ui.mount({ ...PANE, surface: 'desktop' })
  const md = await ui.find({ type: 'Markdown' })
  expect(md?.text).toContain('| winapp |')
  expect(md?.text).not.toContain('C:')
  await ui.unmount()
})
