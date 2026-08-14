export default {
  content: ['./index.html', './src/**/*.{ts,tsx}'],
  theme: {
    extend: {
      fontFamily: {
        sans: [
          '"Alibaba PuHuiTi 3.0"',
          '"Alibaba PuHuiTi"',
          '"OPPO Sans"',
          '"HONOR Sans CN"',
          '"MiSans VF"',
          '"MiSans"',
          '"HarmonyOS Sans SC"',
          '"PingFang SC"',
          '"Microsoft YaHei UI"',
          '"Microsoft YaHei"',
          '"Noto Sans CJK SC"',
          '"Source Han Sans SC"',
          '"Segoe UI Variable"',
          '"Segoe UI"',
          'system-ui',
          'sans-serif',
        ],
        mono: ['"Cascadia Mono"', '"JetBrains Mono"', '"SFMono-Regular"', '"Consolas"', 'monospace'],
      },
      colors: {
        brand: { DEFAULT: 'rgb(var(--xp-brand-rgb) / <alpha-value>)', hover: 'rgb(var(--xp-brand-hover-rgb) / <alpha-value>)' },
        accent: { DEFAULT: 'rgb(var(--xp-brand-rgb) / <alpha-value>)', hover: 'rgb(var(--xp-brand-hover-rgb) / <alpha-value>)' },
        data: { DEFAULT: 'rgb(var(--xp-data-rgb) / <alpha-value>)', soft: 'rgb(var(--xp-data-soft-rgb) / <alpha-value>)' },
        success: 'rgb(var(--xp-success-rgb) / <alpha-value>)',
        warning: 'rgb(var(--xp-warning-rgb) / <alpha-value>)',
        danger: 'rgb(var(--xp-danger-rgb) / <alpha-value>)',
        'error-crimson': 'rgb(var(--xp-danger-rgb) / <alpha-value>)',
        aurora: 'rgb(var(--xp-data-rgb) / <alpha-value>)',
        klein: 'rgb(var(--xp-ink-rgb) / <alpha-value>)',
        ink: 'rgb(var(--xp-ink-rgb) / <alpha-value>)',
        muted: 'rgb(var(--xp-muted-rgb) / <alpha-value>)',
        faint: 'rgb(var(--xp-faint-rgb) / <alpha-value>)',
        ember: 'rgb(var(--xp-danger-rgb) / <alpha-value>)',
        gold: 'rgb(var(--xp-warning-rgb) / <alpha-value>)',
        milk: 'rgb(var(--xp-milk-rgb) / <alpha-value>)',
        'near-black': 'rgb(var(--xp-surface-rgb) / <alpha-value>)',
      },
      borderRadius: {
        sharp: '4px',
        subtle: '6px',
        comfortable: '8px',   // inputs, small controls, switches
        card: '12px',         // cards, insets, tiles
        panel: '14px',        // top-level panels, topbar
      },
    },
  },
  plugins: [],
}
