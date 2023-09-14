#!/usr/bin/env node
// dumb node.js script that fetches and parses the HTML from the given urls
// and passes it to the readability package to clean it up
// The clean HTML is printed to stdout

const { JSDOM } = require("jsdom");
const { Readability } = require('@mozilla/readability');
const timers = require('node:timers/promises');

function parseAndPrint(dom) {
  let reader = new Readability(dom.window.document);
  let article = reader.parse();
  process.stdout.write(JSON.stringify(article));
  process.exit()
}

async function fetchWithPuppeteer(url) {
  const puppeteer = require('puppeteer');
  const browser = await puppeteer.launch({headless: "new"});
  const page = await browser.newPage();
  await page.goto(url);
  await timers.setTimeout(1000);

  const data = await page.evaluate(() => document.querySelector('*').outerHTML);
  return new JSDOM(data);
}

// FIXME  implement cleaner arg parsing
const url = process.argv[2];

if (process.argv[3] === 'puppet') {
  fetchWithPuppeteer(url).then(parseAndPrint);
} else {
  JSDOM.fromURL(url).then(parseAndPrint);
}
