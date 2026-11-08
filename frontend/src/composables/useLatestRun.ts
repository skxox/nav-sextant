import { onBeforeUnmount } from 'vue'

/**
 * 丢弃过期响应。快速切换基金 / 区间时，先发出的请求可能后返回，
 * 直接用会覆盖新数据（图表看起来"卡在旧区间"甚至空白）。
 */
export function useLatestRun() {
  let seq = 0
  onBeforeUnmount(() => { seq += 1 })

  return async function run<T>(task: () => Promise<T>): Promise<{ stale: boolean; value: T }> {
    const id = ++seq
    const value = await task()
    return { stale: id !== seq, value }
  }
}
