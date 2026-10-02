function shuffle(items, rng) {
  const random = rng || Math.random;
  const result = items.slice();
  for (let i = result.length - 1; i > 0; i -= 1) {
    const j = Math.min(i, Math.max(0, Math.floor(random() * (i + 1))));
    const current = result[i];
    result[i] = result[j];
    result[j] = current;
  }
  return result;
}

function chunk(items, size) {
  const width = Math.max(1, Math.floor(size || 5));
  const groups = [];
  for (let index = 0; index < items.length; index += width) groups.push(items.slice(index, index + width));
  return groups;
}

function segments(stem, answers, mode) {
  return String(stem || '').split(/(\{\{\d+\}\})/g).filter(Boolean).map((part, index) => {
    const match = /^\{\{(\d+)\}\}$/.exec(part);
    if (!match) return { key: index, text: part, isAnswer: false };
    const answer = (answers || [])[Number(match[1])];
    return { key: index, text: mode === 'reciting' && answer ? `（${answer}）` : '____', isAnswer: mode === 'reciting' && Boolean(answer) };
  });
}

module.exports = { shuffle, chunk, segments };
