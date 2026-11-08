import * as echarts from 'echarts'
import { nextTick, onBeforeUnmount, onMounted, ref, watch, type Ref } from 'vue'

/**
 * ECharts 挂载。解决三类常见「图表显示不出来」问题：
 *
 * 1. 容器被 v-if 包裹（数据到了才创建 DOM）——选项更新时容器还不存在。
 *    → option / 容器引用都用 flush:'post' 监听，DOM 更新后再 init / setOption。
 * 2. 容器尺寸为 0（隐藏 tab、刚插入布局未完成）时 init 会得到 0×0 画布并永久空白。
 *    → 检测到 0 尺寸时用 requestAnimationFrame 重试若干帧。
 * 3. 容器被重建（v-if 切换 / tab 切换）后旧实例绑在已卸载的 DOM 上。
 *    → 记住绑定的节点，节点变了就 dispose 重建。
 *
 * 另外用 ResizeObserver 替代 window resize 监听（侧边栏折叠、卡片自适应都能跟随），
 * 卸载时统一释放，避免监听器泄漏。
 */
// eslint-disable-next-line @typescript-eslint/no-explicit-any
export function useChart(option: Ref<any>) {
  const el = ref<HTMLElement | null>(null)
  let chart: echarts.ECharts | null = null
  let boundEl: HTMLElement | null = null
  let ro: ResizeObserver | null = null
  let frame = 0
  let retries = 0

  function dispose(): void {
    if (chart && !chart.isDisposed()) chart.dispose()
    chart = null
    boundEl = null
  }

  function observe(node: HTMLElement): void {
    if (typeof ResizeObserver === 'undefined') return
    if (!ro) {
      ro = new ResizeObserver(() => {
        if (chart && !chart.isDisposed() && boundEl && boundEl.clientWidth > 0) chart.resize()
      })
    }
    ro.disconnect()
    ro.observe(node)
  }

  function render(): void {
    const node = el.value
    if (!node) {
      // 容器被 v-if 移除：释放旧实例，等它再次出现时重建
      if (chart) dispose()
      return
    }
    if (chart && boundEl !== node) dispose()

    if (!chart) {
      // 容器还没量出尺寸时 init 会得到空白画布，等下一帧再试
      if ((node.clientWidth === 0 || node.clientHeight === 0) && retries < 30) {
        retries += 1
        cancelAnimationFrame(frame)
        frame = requestAnimationFrame(render)
        return
      }
      chart = echarts.init(node, 'dark', { renderer: 'canvas' })
      boundEl = node
      observe(node)
    }
    retries = 0

    if (!option.value) {
      chart.clear()
      return
    }
    chart.setOption({ backgroundColor: 'transparent', ...option.value }, true)
    if (node.clientWidth > 0 && node.clientHeight > 0) chart.resize()
  }

  function schedule(): void {
    void nextTick(render)
  }

  // 选项变化：DOM 更新后再渲染（容器可能正是这一刻才被创建）
  watch(option, schedule, { flush: 'post' })
  // 容器引用变化：v-if 创建 / 销毁 / 重建
  watch(el, schedule, { flush: 'post' })

  onMounted(() => {
    schedule()
    // 兜底：字体加载 / 布局抖动导致的首帧尺寸异常
    frame = requestAnimationFrame(() => schedule())
  })

  onBeforeUnmount(() => {
    cancelAnimationFrame(frame)
    ro?.disconnect()
    ro = null
    dispose()
  })

  return { el }
}
