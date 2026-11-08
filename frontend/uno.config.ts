import {
  defineConfig,
  presetAttributify,
  presetUno,
  transformerDirectives,
} from 'unocss'

export default defineConfig({
  presets: [presetUno(), presetAttributify()],
  transformers: [transformerDirectives()],
  shortcuts: {
    'card-title': 'text-14px font-semibold flex items-center gap-8px',
    'text-muted-c': 'text-#8b93a5',
    'flex-between': 'flex items-center justify-between',
  },
  theme: {
    colors: {
      accent: '#4d7cfe',
      buy: '#f0506e',
      sell: '#2fbf71',
      hold: '#e0a03c',
      panel: '#131822',
      panel2: '#1a212e',
      line: '#232b3a',
      muted: '#8b93a5',
    },
  },
})
