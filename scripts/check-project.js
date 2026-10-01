const fs = require('node:fs');
const path = require('node:path');
const vm = require('node:vm');
const root = path.join(__dirname, '..');
const miniRoot = path.join(root, 'miniprogram');
const errors = [];
let bytes = 0;
let count = 0;

function walk(directory) {
  for (const entry of fs.readdirSync(directory, { withFileTypes: true })) {
    const filename = path.join(directory, entry.name);
    if (entry.isDirectory()) walk(filename);
    else {
      const source = fs.readFileSync(filename);
      bytes += source.length;
      count += 1;
      if (entry.name.endsWith('.json')) {
        try { JSON.parse(source); } catch (error) { errors.push(`${filename}: ${error.message}`); }
      }
      if (entry.name.endsWith('.js')) {
        try { new vm.Script(source.toString(), { filename }); } catch (error) { errors.push(`${filename}: ${error.message}`); }
      }
    }
  }
}

const config = JSON.parse(fs.readFileSync(path.join(root, 'project.config.json')));
const app = JSON.parse(fs.readFileSync(path.join(miniRoot, 'app.json')));
if (config.miniprogramRoot !== 'miniprogram/') errors.push('miniprogramRoot must exclude ui and doc.');
if (new Set(app.pages).size !== app.pages.length) errors.push('Duplicate page registration.');
for (const page of app.pages) for (const extension of ['js', 'json', 'wxml', 'wxss']) {
  if (!fs.existsSync(path.join(miniRoot, `${page}.${extension}`))) errors.push(`Missing page file: ${page}.${extension}`);
}
for (const component of Object.values(app.usingComponents)) for (const extension of ['js', 'json', 'wxml', 'wxss']) {
  if (!fs.existsSync(path.join(miniRoot, `${component.slice(1)}.${extension}`))) errors.push(`Missing component: ${component}.${extension}`);
}
walk(miniRoot);
const mock = require('../miniprogram/data/mock');
const ids = mock.questions.map((question) => question.id);
if (ids.length !== new Set(ids).size) errors.push('Question IDs must be unique.');
if (mock.articles.length !== 25) errors.push('Prototype directory requires 25 rows.');
for (const question of mock.questions) {
  for (const match of question.stem.matchAll(/\{\{(\d+)\}\}/g)) {
    if (!question.answers[Number(match[1])]) errors.push(`Missing answer: ${question.id}`);
  }
  if (question.articleId && !mock.articles.some((article) => article.id === question.articleId)) errors.push(`Unknown article: ${question.articleId}`);
}
if (bytes > 2 * 1024 * 1024) errors.push('Main package exceeds 2 MiB.');
if (errors.length) { console.error(errors.join('\n')); process.exitCode = 1; }
else console.log(`PASS: ${app.pages.length} pages, ${Object.keys(app.usingComponents).length} components, ${mock.questions.length} sample questions, ${count} files, ${(bytes / 1024).toFixed(1)} KiB.`);
