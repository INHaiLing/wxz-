const test = require('node:test');
const assert = require('node:assert/strict');
const fs = require('node:fs');
const vm = require('node:vm');
const { createRequire } = require('node:module');

const pagePath = require.resolve('../miniprogram/pages/classical/index');
const storePath = require.resolve('../miniprogram/utils/study-store');
const servicePath = require.resolve('../miniprogram/services/data-service');
const navigationPath = require.resolve('../miniprogram/utils/navigation');

function createClassicalPage() {
  let storage;
  let definition;
  const routes = [];
  global.wx = {
    getStorageSync() { return storage; },
    setStorageSync(_key, value) { storage = JSON.parse(JSON.stringify(value)); },
    navigateTo(options) { routes.push(options.url); },
    redirectTo(options) { routes.push(options.url); },
    showToast() {},
    nextTick(callback) { callback(); }
  };
  global.getCurrentPages = () => [{}, {}];
  [storePath, servicePath, navigationPath].forEach((path) => { delete require.cache[path]; });
  vm.runInNewContext(fs.readFileSync(pagePath, 'utf8'), {
    require: createRequire(pagePath),
    Page(value) { definition = value; },
    wx: global.wx
  }, { filename: pagePath });
  const page = {
    ...definition,
    data: JSON.parse(JSON.stringify(definition.data)),
    setData(value) { Object.assign(this.data, value); }
  };
  return { page, store: require(storePath), service: require(servicePath), routes };
}

test('目录续学在字词翻译全掌握后回到可练习篇目，跳过只有常识题的师说', () => {
  const { page, store, service, routes } = createClassicalPage();
  service.getQuestions({ articleId: 'quanxue' }).forEach((question) => store.toggleMastered(question.id));
  assert.equal(service.getQuestions({ articleId: 'shishuo', type: 'fact' }).length, 1);
  assert.equal(service.getQuestions({ articleId: 'shishuo', type: 'word' }).length, 0);
  assert.equal(service.getQuestions({ articleId: 'shishuo', type: 'translation' }).length, 0);
  page.onShow();
  page.onContinue();
  assert.deepEqual(routes, ['/pages/practice/index?articleId=quanxue&type=word']);
});

test('目录续学在字词已掌握而翻译未掌握时进入翻译练习', () => {
  const { page, store, service, routes } = createClassicalPage();
  service.getQuestions({ articleId: 'quanxue', type: 'word' }).forEach((question) => store.toggleMastered(question.id));
  store.saveResume({ path: '/pages/literature/index', options: { categoryId: 'pre-qin' }, groupIds: [], groupIndex: 0 });
  page.onShow();
  page.onContinue();
  assert.deepEqual(routes, ['/pages/practice/index?articleId=quanxue&type=translation']);
});

test('目录续学优先恢复已有练习断点，保留其题型及题序', () => {
  const { page, store, service, routes } = createClassicalPage();
  const words = service.getQuestions({ articleId: 'quanxue', type: 'word' });
  words.forEach((question) => store.toggleMastered(question.id));
  const resume = {
    path: '/pages/practice/index',
    options: { articleId: 'quanxue', type: 'word' },
    groupIds: words.map((question) => question.id).reverse(),
    groupIndex: 0
  };
  store.saveResume(resume);
  page.onShow();
  page.onContinue();
  assert.deepEqual(routes, ['/pages/practice/index?articleId=quanxue&type=word&resume=1']);
  assert.deepEqual(store.getResume(), resume);
});
