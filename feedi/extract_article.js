#!/usr/bin/env node
// node.js script that parses the HTML from the given urls and passes it to the readability package
// to clean it up. If no url is passed, the HTML document is expected from stdin.
// A JSON document is printed to stdout with some metadata and the cleaned up HTML in the 'content' field

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
  allowPositionals: true,
  options: {
    stdin: {type: 'boolean'}
  }
});

const url = positionals[0];
if (!url) {
  process.stderr.write('missing url argument', () => process.exit(1));
}

if (values.stdin) {
  read(process.stdin).then(s => new JSDOM(s, {url})).then(parseAndPrint);
} else {
  JSDOM.fromURL(url).then(parseAndPrint);
}
