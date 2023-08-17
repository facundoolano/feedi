#!/usr/bin/env node

const { JSDOM } = require("jsdom");
const { Readability } = require('@mozilla/readability');

const url = process.argv[2];

JSDOM.fromURL(url).then(function (dom) {
  let reader = new Readability(dom.window.document);
  let article = reader.parse();
  console.log(article.content);
});
