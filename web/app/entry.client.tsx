// SPDX-License-Identifier: MIT
import { startTransition, StrictMode } from "react";
import { hydrateRoot } from "react-dom/client";
import { HydratedRouter } from "react-router/dom";
import { installChunkReload } from "./lib/chunk-reload";

installChunkReload(window);
startTransition(() => {
  hydrateRoot(document, <StrictMode><HydratedRouter /></StrictMode>);
});
