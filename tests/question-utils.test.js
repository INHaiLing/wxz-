const test = require('node:test');
const assert = require('node:assert/strict');
const { shuffle, chunk, segments } = require('../miniprogram/utils/question-utils');
const service = require('../miniprogram/services/data-service');

test('一轮洗牌完整保留题目且不修改输入，末组不补重复题', () => {
  const input = [1, 2, 3, 4, 5, 6, 7];
  const output = shuffle(input, () => 0.25);
  assert.deepEqual(output.slice().sort((a, b) => a - b), input);
  assert.deepEqual(input, [1, 2, 3, 4, 5, 6, 7]);
  assert.deepEqual(chunk(output, 5).map((group) => group.length), [5, 2]);
  assert.deepEqual(chunk([], 5), []);
});

test('背诵展开全部答案，默写不泄露答案', () => {
  const stem = '体例为{{0}}、{{1}}。';
  assert.equal(segments(stem, ['风', '雅'], 'writing').map((part) => part.text).join(''), '体例为____、____。');
  assert.equal(segments(stem, ['风', '雅'], 'reciting').filter((part) => part.isAnswer).length, 2);
});

test('两种随机入口题源分离，收藏文言分类能取到对应题型', () => {
  const literature = service.getQuestions({ source: 'literature' });
  const classical = service.getQuestions({ source: 'classical' });
  assert.ok(literature.length > 0 && classical.length > 0);
  assert.ok(literature.every((question) => question.source === 'literature'));
  assert.ok(classical.every((question) => question.source === 'classical'));
  assert.ok(service.getQuestions({ categoryId: 'classical-translation' }).every((question) => question.type === 'translation'));
  assert.equal(service.getQuestions({ articleId: 'guaren', type: 'word' }).length, 0);
});
