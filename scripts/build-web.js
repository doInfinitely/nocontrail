#!/usr/bin/env node
// Build the browser version into site/app/. The real script lives in
// site/build.js because Vercel runs it as the project's build command; this
// wrapper keeps the local entry point where the other repo scripts are.
require("../site/build.js");
