'use strict';
const express = require('express');
const PORT = 8080;
const HOST = '0.0.0.0';

// Configurable value, set via Pulumi and passed in as env var
const MESSAGE = process.env.MESSAGE || 'Hello from default';

const app = express();
app.get('/', (_req, res) => {
  res.send(`<h1>Pulumi App</h1><p>Configurable value: <b>${MESSAGE}</b></p>`);
});
app.listen(PORT, HOST, () => {
  console.log(`Running on http://${HOST}:${PORT}`);
});
