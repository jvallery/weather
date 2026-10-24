// SPDX-License-Identifier: MIT
import express from "express";
import { createRequestHandler } from "@react-router/express";
import { collectDefaultMetrics, register } from "prom-client";

collectDefaultMetrics();
const metrics = express();
metrics.disable("x-powered-by");
metrics.get("/metrics", async (_req, res) => {
  res.type(register.contentType).send(await register.metrics());
});
metrics.listen(Number(process.env.METRICS_PORT || 9000), "0.0.0.0");
const app = express();
app.disable("x-powered-by");
app.get(["/healthz", "/readyz"], (_req, res) => res.json({ status: "ok", service: "weather-web" }));
app.get("/metrics", (_req, res) => res.sendStatus(404));
app.use(express.static("build/client", { immutable: true, maxAge: "1y" }));
app.use(createRequestHandler({ build: () => import("./build/server/index.js") }));
app.listen(Number(process.env.PORT || 3000), "0.0.0.0");
