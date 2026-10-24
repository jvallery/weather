// SPDX-License-Identifier: MIT
import { defineConfig } from "vitest/config";
export default defineConfig({ test: { maxWorkers: 1, include: ["app/**/*.test.tsx"] } });
