const test = require('node:test');
const assert = require('node:assert/strict');
const mock = require('../miniprogram/data/mock');
const storePath = require.resolve('../miniprogram/utils/study-store');
const servicePath = require.resolve('../miniprogram/services/data-service');

function createStore(saved, failSave) {
  let storage = saved;
  const notices = [];
  global.wx = {
    getStorageSync() { return storage; },
    setStorageSync(_key, value) { if (failSave) throw new Error('storage full'); storage = JSON.parse(JSON.stringify(value)); },
    showToast(value) { notices.push(value.title); }
  };
  delete require.cache[storePath];
  delete require.cache[servicePath];
  const store = require(storePath);
  return { store, read: () => storage, notices };
}

test('收藏与掌握分离，跨页读取并能重启恢复', () => {
  const { store, read } = createStore();
  const id = mock.questions[0].id;
  store.toggleFavorite(id);
  assert.equal(store.getStats().masteredCount, 0);
  store.toggleMastered(id);
  const service = require(servicePath);
  const decorated = service.decorateQuestions([service.getQuestion(id)])[0];
  assert.equal(decorated.favorite, true);
  assert.equal(decorated.mastered, true);
  const saved = read();
  const reloaded = createStore(saved).store;
  assert.deepEqual(reloaded.getState().favorites, [id]);
  assert.equal(reloaded.getStats().masteredCount, 1);
});

test('掌握操作按题去重，取消不会清除历史学习天数', () => {
  const { store } = createStore();
  const id = mock.questions[0].id;
  store.toggleMastered(id);
  store.toggleMastered(id);
  store.toggleMastered(id);
  assert.equal(store.getStats().todayCount, 1);
  assert.equal(store.getStats().studyDays, 1);
  store.toggleMastered(id);
  assert.equal(store.getStats().todayCount, 0);
  assert.equal(store.getStats().studyDays, 1);
});

test('学习记录无效ID不计数，损坏存储采用默认状态', () => {
  let store = createStore('invalid json').store;
  assert.equal(store.getStats().completion, 0);
  store = createStore({ version: 1, favorites: ['missing', mock.questions[0].id, mock.questions[0].id], mastered: { missing: '2026-10-01' }, studyDates: ['invalid'], mode: 'unknown' }).store;
  assert.equal(store.getStats().masteredCount, 0);
  assert.equal(store.getStats().favoriteCount, 1);
  assert.equal(store.getMode(), 'writing');
  assert.equal(store.toggleMastered('missing'), false);
});

test('存储失败保留当前操作且给出提示', () => {
  const { store, notices } = createStore(null, true);
  store.toggleFavorite(mock.questions[0].id);
  assert.equal(store.getStats().favoriteCount, 1);
  assert.equal(notices.length, 1);
});

test('模式和随机本轮顺序可以恢复', () => {
  const { store, read } = createStore();
  const ids = mock.questions.filter((question) => question.source === 'literature').map((question) => question.id);
  store.setMode('reciting');
  store.saveResume({ path: '/pages/random/index', options: { source: 'literature' }, groupIds: ids, groupIndex: 1 });
  const reloaded = createStore(read()).store;
  assert.equal(reloaded.getMode(), 'reciting');
  assert.deepEqual(reloaded.getResume().groupIds, ids);
  assert.equal(reloaded.getResume().groupIndex, 1);
});

test('目录无题篇目保持未学，有题篇目按实际题目掌握', () => {
  const { store } = createStore();
  const service = require(servicePath);
  service.getQuestions({ articleId: 'quanxue' }).forEach((question) => store.toggleMastered(question.id));
  const articles = service.getArticleProgress();
  assert.equal(articles.find((article) => article.id === 'quanxue').status, 'learned');
  assert.equal(articles.find((article) => article.id === 'guaren').status, 'unlearned');
  assert.equal(store.getStats().articleLearnedCount, 1);
  assert.equal(store.getStats().articleTotal, 25);
});
