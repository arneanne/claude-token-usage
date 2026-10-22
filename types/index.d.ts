export type Totals = {
  input: number
  output: number
  cache_write: number
  cache_read: number
  messages: number
  total: number
  cost: number
}

export type Row = Totals & { model?: string; project?: string }

export type Summary = {
  generated: string
  session: Totals
  today: Totals
  week: Totals
  month: Totals
  all: Totals
  daily: { day: string; cost: number }[]
  by_model: Row[]
  by_project: Row[]
}

declare module 'claude-code' {
  interface PluginState {
    'token-usage': { summary: Summary | null; error: string | null }
  }
}
