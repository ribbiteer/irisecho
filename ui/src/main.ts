// SPDX-License-Identifier: AGPL-3.0-or-later
import { mount } from "svelte";
import "./app.css";
import App from "./App.svelte";
import { routeExternalLinks } from "./lib/shell";
import { start } from "./lib/state.svelte";

routeExternalLinks();
start();
mount(App, { target: document.getElementById("app")! });
