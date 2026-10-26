/**
 * Shared Prettier base. Consumers that use Tailwind should set `tailwindStylesheet`
 * relative to their own config file (see the root .prettierrc.mjs).
 * @type {import("prettier").Config}
 */
const config = {
  semi: true,
  singleQuote: false,
  trailingComma: "all",
  printWidth: 100,
  tabWidth: 2,
  plugins: ["prettier-plugin-tailwindcss"],
  tailwindFunctions: ["cn", "cva"],
};

export default config;
