const test = require('node:test');
const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const vm = require('node:vm');
const { createRequire } = require('node:module');
const mock = require('../miniprogram/data/mock');

function createCard() {
  const filename = path.join(__dirname, '../miniprogram/components/question-card/index.js');
  let definition;
  vm.runInNewContext(fs.readFileSync(filename, 'utf8'), {
    require: createRequire(filename), Component(value) { definition = value; }
  }, { filename });
  const card = { data: { ...definition.data }, setData(value) { Object.assign(this.data, value); } };
  card.render = (question, mode) => definition.observers['question, mode'].call(card, question, mode);
  return card;
}

test('翻译背诵保留完整原句与译文，不修改题库', () => {
  const card = createCard();
  const question = mock.questions.find((item) => item.id === 'translation-qing');
  const original = JSON.stringify(question);
  card.render(question, 'reciting');
  assert.equal(card.data.isTranslation, true);
  assert.equal(card.data.translationStem, '青，取之于蓝，而青于蓝。');
  assert.equal(card.data.translationAnswer, question.answers[0]);
  assert.equal(JSON.stringify(question), original);
});

test('从背诵切回默写会清空翻译答案和可见答案片段', () => {
  const card = createCard();
  const question = mock.questions.find((item) => item.type === 'translation');
  card.render(question, 'reciting');
  card.render(question, 'writing');
  assert.equal(card.data.translationAnswer, '');
  assert.ok(card.data.segments.every((part) => !part.isAnswer));
  assert.ok(!card.data.segments.map((part) => part.text).join('').includes(question.answers[0]));
});

test('随机题卡复用为普通题时清理翻译状态并保留全部答案', () => {
  const card = createCard();
  card.render(mock.questions.find((item) => item.type === 'translation'), 'reciting');
  const question = mock.questions.find((item) => item.id === 'lit-shijing-six');
  card.render(question, 'reciting');
  assert.equal(card.data.isTranslation, false);
  assert.equal(card.data.translationStem, '');
  assert.equal(card.data.translationAnswer, '');
  const answers = card.data.segments.filter((part) => part.isAnswer).map((part) => part.text);
  assert.equal(JSON.stringify(answers), JSON.stringify(question.answers));
});
