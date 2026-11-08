/** 金融图表共享主题：暗色、网格、十字准线、涨红跌绿（中国市场惯例）。 */

export const UP_COLOR = '#ef5350'    // 涨：红
export const DOWN_COLOR = '#26a69a'  // 跌：绿
export const MA5_COLOR = '#f7b500'
export const MA20_COLOR = '#4d7cfe'
export const MA60_COLOR = '#a78bfa'
export const ACCENT = '#4d7cfe'

export const axisLabelStyle = {
  fontSize: 10,
  color: 'rgba(255,255,255,0.45)',
}

export const axisLineStyle = {
  lineStyle: { color: 'rgba(255,255,255,0.12)' },
}

export const splitLineStyle = {
  lineStyle: { color: 'rgba(255,255,255,0.06)', type: 'dashed' as const },
}

export function baseTooltip(extra: Record<string, unknown> = {}): Record<string, unknown> {
  return {
    trigger: 'axis',
    axisPointer: { type: 'cross', crossStyle: { color: 'rgba(255,255,255,0.25)' } },
    backgroundColor: 'rgba(20,24,34,0.95)',
    borderColor: 'rgba(255,255,255,0.10)',
    textStyle: { color: 'rgba(255,255,255,0.85)', fontSize: 11 },
    ...extra,
  }
}

export function baseGrid(top = 24, bottom = 64, left = 60, right = 16): Record<string, number> {
  return { left, right, top, bottom }
}

// eslint-disable-next-line @typescript-eslint/no-explicit-any
export function dataZoomCfg(bottom = 10, height = 18): any[] {
  return [
    { type: 'inside' },
    { type: 'slider', height, bottom, borderColor: 'rgba(255,255,255,0.08)',
      backgroundColor: 'rgba(255,255,255,0.03)',
      fillerColor: 'rgba(77,124,254,0.15)',
      handleStyle: { color: '#4d7cfe' },
      textStyle: { color: 'rgba(255,255,255,0.4)', fontSize: 9 } },
  ]
}

/** 简单移动平均 */
export function ma(values: number[], window: number): (number | null)[] {
  const out: (number | null)[] = []
  let sum = 0
  for (let i = 0; i < values.length; i++) {
    sum += values[i]
    if (i >= window) sum -= values[i - window]
    out.push(i >= window - 1 ? Number((sum / window).toFixed(4)) : null)
  }
  return out
}

export function tooltipNumber(v: number | null | undefined, digits = 2): string {
  return v == null ? '—' : v.toFixed(digits)
}
