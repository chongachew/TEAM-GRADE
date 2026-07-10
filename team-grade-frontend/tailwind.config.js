/** @type {import('tailwindcss').Config} */
module.exports = {
  content: ["./src/**/*.{js,jsx}", "./public/index.html"],
  theme: {
    extend: {
      colors: {
        // Same brand tokens as the-bridge.app's Tailwind config - shared visual
        // language across the two separately-deployed apps, per the blueprint's
        // "one unified brand through design tokens, not shared components".
        bridge: {
          navy: "#1E3A5F",
          blue: "#1D4ED8",
          gold: "#F59E0B",
        },
      },
      fontFamily: {
        // Condensed scoreboard type for stats/labels, per the wireframe - a
        // system stack, not a webfont, to avoid a network dependency/FOUC.
        condensed: ["'Arial Narrow'", "'Helvetica Neue Condensed'", "sans-serif"],
      },
    },
  },
  plugins: [],
}

