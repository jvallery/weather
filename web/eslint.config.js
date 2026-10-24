// SPDX-License-Identifier: MIT
import js from "@eslint/js";
import tseslint from "typescript-eslint";
export default tseslint.config(
  { ignores: ["build/**", ".react-router/**", "node_modules/**", "app/generated/**"] },
  js.configs.recommended,
  ...tseslint.configs.recommended,
  { files: ["**/*.mjs"], languageOptions: { globals: { process: "readonly", Buffer: "readonly" } } },
);
