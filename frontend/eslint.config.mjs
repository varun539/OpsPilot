import next from "eslint-config-next";

const config = [
  ...next,
  {
    ignores: [
      ".next/**",
      ".next-unconfigured/**",
      "out/**",
      "node_modules/**",
      "coverage/**",
    ],
  },
];

export default config;
