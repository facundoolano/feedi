#!/usr/bin/env node
// dumb node.js script that fetches and parses the HTML from the given urls
// and passes it to the readability package to clean it up
// The clean HTML is printed to stdout

const { JSDOM } = require("jsdom");
const { Readability } = require('@mozilla/readability');
const util = require('node:util');

function parseAndPrint(dom) {
  let reader = new Readability(dom.window.document);
  let article = reader.parse();
  process.stdout.write(JSON.stringify(article), process.exit);
}

async function read(stream) {
  const chunks = [];
  for await (const chunk of stream) chunks.push(chunk);
  return Buffer.concat(chunks).toString('utf8');
}

const {values, positionals} =  util.parseArgs({
  allowPositionals: true
});
const url = positionals[0];

if (url) {
  JSDOM.fromURL(url).then(parseAndPrint);
} else {
  read(process.stdin).then(s => new JSDOM(s)).then(parseAndPrint);
}
