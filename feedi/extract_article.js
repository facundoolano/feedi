#!/usr/bin/env node
// dumb node.js script that fetches and parses the HTML from the given urls
// and passes it to the readability package to clean it up
// The clean HTML is printed to stdout

const { JSDOM } = require("jsdom");
const { Readability } = require('@mozilla/readability');

const url = process.argv[2];

JSDOM.fromURL(url).then(function (dom) {
  let reader = new Readability(dom.window.document);
  let article = reader.parse();
  console.log(article.content);
});
