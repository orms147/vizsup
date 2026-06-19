/** @type {import('tailwindcss').Config} */
export default {
  content: ["./index.html", "./src/**/*.{ts,tsx}"],
  theme: {
    extend: {
      fontFamily: {
        // Vietnamese-diacritic capable UI font
        sans: ["Be Vietnam Pro", "Inter", "Noto Sans", "system-ui", "sans-serif"],
      },
    },
  },
  plugins: [],
};
