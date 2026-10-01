const mock = require('../data/mock');
const store = require('../utils/study-store');

function getCategories(source) {
  const result = mock.categories.map((category) => ({ ...category }));
  if (source === 'favorites') return result.concat([
    { id: 'classical-word', name: '文言字词' },
    { id: 'classical-translation', name: '文言翻译' },
    { id: 'classical-fact', name: '文言常识' }
  ]);
  return result;
}

function getArticles() { return mock.articles.map((article) => ({ ...article })); }
function getQuestions(filters) {
  const query = filters || {};
  return mock.questions.filter((question) => {
    if (query.source && question.source !== query.source) return false;
    if (query.articleId && question.articleId !== query.articleId) return false;
    if (query.type && question.type !== query.type) return false;
    if (query.categoryId) {
      if (query.categoryId.indexOf('classical-') === 0) {
        if (question.source !== 'classical' || question.type !== query.categoryId.slice(10)) return false;
      } else if (question.categoryId !== query.categoryId) return false;
    }
    return true;
  }).map((question) => ({ ...question, answers: question.answers.slice() }));
}
function getQuestion(id) { return getQuestions().find((question) => question.id === id); }
function decorateQuestions(items) {
  const state = store.getState();
  return items.map((question) => ({ ...question, favorite: state.favorites.indexOf(question.id) !== -1, mastered: Boolean(state.mastered[question.id]) }));
}
function getArticleProgress() {
  const state = store.getState();
  return getArticles().map((article) => {
    const items = getQuestions({ articleId: article.id });
    const completed = items.filter((question) => state.mastered[question.id]).length;
    const status = completed === 0 ? 'unlearned' : completed === items.length ? 'learned' : 'learning';
    return { ...article, completed, total: items.length, status, statusLabel: { unlearned: '未学', learning: '练习中', learned: '已学' }[status] };
  });
}

module.exports = { getCategories, getArticles, getQuestions, getQuestion, decorateQuestions, getArticleProgress };
