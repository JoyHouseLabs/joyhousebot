/** @type {import('tailwindcss').Config} */
export default {
  content: [
    './components/**/*.{vue,ts}',
    './layouts/**/*.vue',
    './pages/**/*.vue',
    './data/**/*.ts',
    './app.vue',
  ],
  theme: {
    extend: {
      colors: {
        bot: {
          DEFAULT: '#6b5cf6',
          deep: '#5546dc',
          soft: '#f1efff',
          line: '#ded9ff',
          ink: '#242127',
          muted: '#777382',
          faint: '#a7a2b0',
          warm: '#fbf8f4',
        },
      },
      backgroundImage: {
        'bot-gradient': 'linear-gradient(135deg, #6b5cf6 0%, #8468ff 54%, #ee4e9b 125%)',
      },
      borderRadius: {
        card: '1.6rem',
        panel: '2rem',
      },
      boxShadow: {
        card: '0 14px 44px rgba(55, 45, 120, 0.09)',
        float: '0 24px 70px rgba(48, 38, 105, 0.16)',
      },
    },
  },
  plugins: [],
}
